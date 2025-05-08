import os
import random
import json
import threading
from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__, static_url_path='/static')
app.config['SECRET_KEY'] = 'your_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")

with open('champions.json', encoding='utf-8') as f:
    champions = json.load(f)
champion_image_map = {c['name']: c['image'] for c in champions}

rooms = {}
previous_picks = []

BLUE_FIRST_PICK_ORDER = ['blue', 'red', 'red', 'blue', 'blue', 'red', 'red', 'blue', 'blue', 'red']
RED_FIRST_PICK_ORDER = ['red', 'blue', 'blue', 'red', 'red', 'blue', 'blue', 'red', 'red', 'blue']

def get_pick_order(room_id):
    first_pick_team = rooms[room_id]['state']['first_pick_team']
    return BLUE_FIRST_PICK_ORDER if first_pick_team == 'blue' else RED_FIRST_PICK_ORDER

def generate_random_bans():
    available = [c for c in champions if c['name'] not in previous_picks]
    return random.sample(available, min(10, len(available)))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/rooms')
def rooms_page():
    return render_template('rooms.html', rooms=rooms)

@app.route('/create_room', methods=['POST'])
def create_room():
    room_name = request.form.get('room_name')
    room_id = str(random.randint(1000, 9999))
    rooms[room_id] = {
        'name': room_name,
        'blue_team': None,
        'red_team': None,
        'state': {
            'current_team': 'blue',
            'pick_phase': 0,
            'blue_picks': [],
            'red_picks': [],
            'random_bans': generate_random_bans(),
            'first_pick_team': 'blue',
            'timer_thread': None,
            'timer_stop': False,
        }
    }
    return redirect(url_for('draft', room_id=room_id))

@app.route('/draft/<room_id>')
def draft(room_id):
    if room_id not in rooms:
        return redirect(url_for('rooms_page'))
    team = request.args.get('team', 'spectator')
    session['team'] = team
    session['room_id'] = room_id
    if team == 'blue' and not rooms[room_id]['blue_team']:
        rooms[room_id]['blue_team'] = request.remote_addr
    elif team == 'red' and not rooms[room_id]['red_team']:
        rooms[room_id]['red_team'] = request.remote_addr
    pick_order = get_pick_order(room_id)
    return render_template('draft.html',
        room_id=room_id,
        room=rooms[room_id],
        champions=champions,
        previous_picks=previous_picks,
        champion_image_map=champion_image_map,
        team=team,
        pick_order=pick_order
    )

@socketio.on('join')
def on_join(data):
    room_id = data['room']
    team = data.get('team', 'spectator')
    join_room(room_id)
    emit('status', {'msg': f'{team} 팀이 입장했습니다.'}, room=room_id)
    if room_id in rooms:
        first_pick_team = rooms[room_id]['state']['first_pick_team']
        emit('update_draft', {
            'blue_picks': rooms[room_id]['state']['blue_picks'],
            'red_picks': rooms[room_id]['state']['red_picks'],
            'current_team': rooms[room_id]['state']['current_team'],
            'pick_phase': rooms[room_id]['state']['pick_phase'],
            'random_bans': [c['name'] for c in rooms[room_id]['state']['random_bans']],
            'first_pick_team': first_pick_team,
            'pick_order': get_pick_order(room_id)
        }, room=request.sid)

def start_pick_timer(room_id):
    def timer_thread():
        phase = rooms[room_id]['state']['pick_phase']
        pick_order = get_pick_order(room_id)
        if phase >= len(pick_order):
            return
        for t in range(1, 41):
            if rooms[room_id]['state'].get('timer_stop'):
                return
            socketio.sleep(1)
            # 30초에는 아무것도 하지 않음
            if t == 40:
                # 40초 랜덤 픽
                force_random_pick(room_id)
                return
    # 이전 타이머 중지
    rooms[room_id]['state']['timer_stop'] = True
    # 새 타이머 시작
    rooms[room_id]['state']['timer_stop'] = False
    thread = threading.Thread(target=timer_thread, daemon=True)
    rooms[room_id]['state']['timer_thread'] = thread
    thread.start()

def force_random_pick(room_id):
    state = rooms[room_id]['state']
    pick_order = get_pick_order(room_id)
    phase = state['pick_phase']
    if phase >= len(pick_order):
        return
    team = pick_order[phase]
    picked = state['blue_picks'] + state['red_picks'] + [c['name'] for c in state['random_bans']] + previous_picks
    available = [c['name'] for c in champions if c['name'] not in picked]
    if available:
        random_pick = random.choice(available)
        _server_pick(room_id, team, random_pick)

@socketio.on('pick_champion')
def on_pick(data):
    room_id = data['room']
    team = data['team']
    champion = data['champion']
    _server_pick(room_id, team, champion)

def _server_pick(room_id, team, champion):
    if room_id not in rooms:
        return
    room = rooms[room_id]
    state = room['state']
    current_phase = state['pick_phase']
    pick_order = get_pick_order(room_id)
    if current_phase >= len(pick_order):
        return
    current_team = pick_order[current_phase]
    all_selected = (state['blue_picks'] + state['red_picks'] +
                    [c['name'] for c in state['random_bans']] +
                    previous_picks)
    if champion in all_selected:
        return
    if current_team != team:
        return
    # 픽 추가
    if team == 'blue':
        state['blue_picks'].append(champion)
    else:
        state['red_picks'].append(champion)
    state['pick_phase'] += 1
    if state['pick_phase'] < len(pick_order):
        state['current_team'] = pick_order[state['pick_phase']]
    # 타이머 리셋
    state['timer_stop'] = True
    # 모든 클라이언트에 업데이트 전송
    socketio.emit('update_draft', {
        'blue_picks': state['blue_picks'],
        'red_picks': state['red_picks'],
        'current_team': state['current_team'],
        'pick_phase': state['pick_phase'],
        'random_bans': [c['name'] for c in state['random_bans']],
        'first_pick_team': state['first_pick_team'],
        'pick_order': pick_order
    }, room=room_id)
    # 타이머 재시작(다음 픽이 남아있으면)
    if state['pick_phase'] < len(pick_order):
        socketio.emit('restart_timer', {'duration': 40}, room=room_id)
        start_pick_timer(room_id)

if __name__ == '__main__':
    import eventlet
    eventlet.monkey_patch()
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
