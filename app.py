from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import random, json, uuid, os, threading

app = Flask(__name__, static_url_path='/static')
app.config['SECRET_KEY'] = 'your_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")

# 챔피언 데이터 로드
try:
    with open('champions.json', encoding='utf-8') as f:
        champions = json.load(f)
    champion_image_map = {c['name']: c['image'] for c in champions}
except FileNotFoundError:
    print("champions.json 파일이 없습니다. utils/riot_api.py를 먼저 실행하세요.")
    champions = []
    champion_image_map = {}

rooms = {}
aram_rooms = {}
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
    room_id = str(uuid.uuid4())
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

@app.route('/aram_rooms')
def aram_rooms_page():
    return render_template('aram_rooms.html', rooms=aram_rooms)

@app.route('/create_aram_room', methods=['POST'])
def create_aram_room():
    room_name = request.form.get('room_name')
    room_id = str(uuid.uuid4())
    all_champions = [c['name'] for c in champions]
    random.shuffle(all_champions)
    aram_rooms[room_id] = {
        'name': room_name,
        'blue_team': None,
        'red_team': None,
        'blue_members': [],
        'red_members': [],
        'available_champs': all_champions.copy(),
        'reroll_pool_blue': [],
        'reroll_pool_red': [],
        'all_champs_history': {},
        'game_started': False
    }
    return redirect(url_for('aram_rooms_page'))

@app.route('/aram_setup/<room_id>')
def aram_setup(room_id):
    if room_id not in aram_rooms:
        return redirect(url_for('aram_rooms_page'))
    team = request.args.get('team', 'spectator')
    session['team'] = team
    session['room_id'] = room_id
    if team == 'blue' and not aram_rooms[room_id]['blue_team']:
        aram_rooms[room_id]['blue_team'] = request.remote_addr
    elif team == 'red' and not aram_rooms[room_id]['red_team']:
        aram_rooms[room_id]['red_team'] = request.remote_addr
    return render_template('aram_setup.html',
        room_id=room_id,
        room=aram_rooms[room_id],
        team=team
    )

@app.route('/aram/<room_id>')
def aram(room_id):
    if room_id not in aram_rooms:
        return redirect(url_for('aram_rooms_page'))
    team = request.args.get('team', 'spectator')
    session['team'] = team
    session['room_id'] = room_id
    return render_template('aram.html',
        room_id=room_id,
        room=aram_rooms[room_id],
        champions=champions,
        champion_image_map=champion_image_map,
        team=team
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
    elif room_id in aram_rooms:
        emit('update_aram', aram_rooms[room_id], room=request.sid)

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
            if t == 40:
                force_random_pick(room_id)
                return
    rooms[room_id]['state']['timer_stop'] = True
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
    if champion in all_selected or current_team != team:
        return
    if team == 'blue':
        state['blue_picks'].append(champion)
    else:
        state['red_picks'].append(champion)
    state['pick_phase'] += 1
    if state['pick_phase'] < len(pick_order):
        state['current_team'] = pick_order[state['pick_phase']]
    state['timer_stop'] = True
    socketio.emit('update_draft', {
        'blue_picks': state['blue_picks'],
        'red_picks': state['red_picks'],
        'current_team': state['current_team'],
        'pick_phase': state['pick_phase'],
        'random_bans': [c['name'] for c in state['random_bans']],
        'first_pick_team': state['first_pick_team'],
        'pick_order': pick_order
    }, room=room_id)
    if state['pick_phase'] < len(pick_order):
        socketio.emit('restart_timer', {'duration': 30}, room=room_id)
        start_pick_timer(room_id)

@socketio.on('finish_game')
def on_finish_game(data):
    room_id = data['room']
    if room_id not in rooms:
        return
    room = rooms[room_id]
    global previous_picks
    previous_picks.extend(room['state']['blue_picks'])
    previous_picks.extend(room['state']['red_picks'])
    if len(previous_picks) > 50:
        previous_picks = previous_picks[-50:]
    next_first_pick = 'red' if room['state']['first_pick_team'] == 'blue' else 'blue'
    room['state'] = {
        'current_team': next_first_pick,
        'pick_phase': 0,
        'blue_picks': [],
        'red_picks': [],
        'random_bans': generate_random_bans(),
        'first_pick_team': next_first_pick
    }
    next_pick_order = BLUE_FIRST_PICK_ORDER if next_first_pick == 'blue' else RED_FIRST_PICK_ORDER
    socketio.emit('game_finished', {
        'previous_picks': previous_picks,
        'new_state': room['state'],
        'first_pick_team': next_first_pick,
        'pick_order': next_pick_order
    }, room=room_id)

@socketio.on('reset_game')
def on_reset_game(data):
    room_id = data['room']
    if room_id not in rooms:
        return
    room = rooms[room_id]
    global previous_picks
    previous_picks = []
    room['state'] = {
        'current_team': 'blue',
        'pick_phase': 0,
        'blue_picks': [],
        'red_picks': [],
        'random_bans': generate_random_bans(),
        'first_pick_team': 'blue'
    }
    socketio.emit('game_reset', {
        'previous_picks': [],
        'new_state': room['state'],
        'first_pick_team': 'blue',
        'pick_order': BLUE_FIRST_PICK_ORDER
    }, room=room_id)

# --- ARAM 기능 ---
@socketio.on('add_aram_member')
def handle_add_aram_member(data):
    room_id = data['room']
    team = data['team']
    nickname = data['nickname']
    if room_id not in aram_rooms:
        return
    room = aram_rooms[room_id]
    if (team == 'blue' and room['blue_team'] != request.remote_addr) or \
       (team == 'red' and room['red_team'] != request.remote_addr):
        emit('error', {'msg': '해당 팀의 권한이 없습니다.'}, room=request.sid)
        return
    team_key = 'blue_members' if team == 'blue' else 'red_members'
    if room['available_champs']:
        champ = random.choice(room['available_champs'])
        room['available_champs'].remove(champ)
        room[team_key].append({
            'name': nickname,
            'champion': champ,
            'reroll_count': 1
        })
        room['all_champs_history'][champ] = {'team': team, 'original': True}
        emit('update_aram', room, room=room_id)
    else:
        emit('error', {'msg': '사용 가능한 챔피언이 없습니다.'}, room=request.sid)

@socketio.on('remove_aram_member')
def handle_remove_aram_member(data):
    room_id = data['room']
    team = data['team']
    idx = data['index']
    if room_id not in aram_rooms:
        return
    room = aram_rooms[room_id]
    if (team == 'blue' and room['blue_team'] != request.remote_addr) or \
       (team == 'red' and room['red_team'] != request.remote_addr):
        emit('error', {'msg': '해당 팀의 권한이 없습니다.'}, room=request.sid)
        return
    team_key = 'blue_members' if team == 'blue' else 'red_members'
    if idx < len(room[team_key]):
        member = room[team_key][idx]
        if member['champion']:
            room['available_champs'].append(member['champion'])
            if member['champion'] in room['all_champs_history']:
                del room['all_champs_history'][member['champion']]
        room[team_key].pop(idx)
        emit('update_aram', room, room=room_id)

@socketio.on('roll_champion')
def handle_roll_champion(data):
    room_id = data['room']
    team = data['team']
    idx = data['index']
    if room_id not in aram_rooms:
        return
    room = aram_rooms[room_id]
    if (team == 'blue' and room['blue_team'] != request.remote_addr) or \
       (team == 'red' and room['red_team'] != request.remote_addr):
        emit('error', {'msg': '해당 팀의 권한이 없습니다.'}, room=request.sid)
        return
    team_key = 'blue_members' if team == 'blue' else 'red_members'
    reroll_pool_key = 'reroll_pool_blue' if team == 'blue' else 'reroll_pool_red'
    if idx >= len(room[team_key]):
        emit('error', {'msg': '잘못된 멤버 인덱스입니다.'}, room=request.sid)
        return
    member = room[team_key][idx]
    if member['reroll_count'] <= 0:
        emit('error', {'msg': '리롤 횟수를 모두 사용했습니다.'}, room=request.sid)
        return
    if member['champion']:
        current_champ = member['champion']
        room[reroll_pool_key].append(current_champ)
        room['all_champs_history'][current_champ] = {'team': team, 'original': False}
    if room['available_champs']:
        new_champ = random.choice(room['available_champs'])
        room['available_champs'].remove(new_champ)
        member['champion'] = new_champ
        member['reroll_count'] -= 1
        room['all_champs_history'][new_champ] = {'team': team, 'original': True}
    emit('update_aram', room, room=room_id)

@socketio.on('select_from_reroll_pool')
def handle_select_from_reroll_pool(data):
    room_id = data['room']
    team = data['team']
    idx = data['index']
    champion = data['champion']
    if room_id not in aram_rooms:
        return
    room = aram_rooms[room_id]
    if (team == 'blue' and room['blue_team'] != request.remote_addr) or \
       (team == 'red' and room['red_team'] != request.remote_addr):
        emit('error', {'msg': '해당 팀의 권한이 없습니다.'}, room=request.sid)
        return
    team_key = 'blue_members' if team == 'blue' else 'red_members'
    reroll_pool_key = 'reroll_pool_blue' if team == 'blue' else 'reroll_pool_red'
    if idx >= len(room[team_key]):
        emit('error', {'msg': '잘못된 멤버 인덱스입니다.'}, room=request.sid)
        return
    member = room[team_key][idx]
    if champion not in room[reroll_pool_key]:
        emit('error', {'msg': '선택한 챔피언이 리롤 풀에 없습니다.'}, room=request.sid)
        return
    if member['champion']:
        current_champ = member['champion']
        room[reroll_pool_key].append(current_champ)
        room['all_champs_history'][current_champ] = {'team': team, 'original': False}
    member['champion'] = champion
    room[reroll_pool_key].remove(champion)
    room['all_champs_history'][champion] = {'team': team, 'original': False}
    emit('update_aram', room, room=room_id)

@socketio.on('swap_champions')
def handle_swap_champions(data):
    room_id = data['room']
    source_team = data['source_team']
    source_idx = data['source_idx']
    target_team = data['target_team']
    target_idx = data['target_idx']
    if room_id not in aram_rooms:
        return
    room = aram_rooms[room_id]
    if (source_team == 'blue' and room['blue_team'] != request.remote_addr) or \
       (source_team == 'red' and room['red_team'] != request.remote_addr):
        emit('error', {'msg': '해당 팀의 권한이 없습니다.'}, room=request.sid)
        return
    source_team_key = 'blue_members' if source_team == 'blue' else 'red_members'
    target_team_key = 'blue_members' if target_team == 'blue' else 'red_members'
    if source_idx >= len(room[source_team_key]) or target_idx >= len(room[target_team_key]):
        emit('error', {'msg': '잘못된 멤버 인덱스입니다.'}, room=request.sid)
        return
    source_member = room[source_team_key][source_idx]
    target_member = room[target_team_key][target_idx]
    source_champ = source_member['champion']
    target_champ = target_member['champion']
    source_member['champion'] = target_champ
    target_member['champion'] = source_champ
    if source_champ in room['all_champs_history']:
        room['all_champs_history'][source_champ]['team'] = target_team
    if target_champ in room['all_champs_history']:
        room['all_champs_history'][target_champ]['team'] = source_team
    emit('update_aram', room, room=room_id)

@socketio.on('update_nickname')
def handle_update_nickname(data):
    room_id = data['room']
    team = data['team']
    idx = data['index']
    name = data['name']
    if room_id not in aram_rooms:
        return
    room = aram_rooms[room_id]
    if (team == 'blue' and room['blue_team'] != request.remote_addr) or \
       (team == 'red' and room['red_team'] != request.remote_addr):
        emit('error', {'msg': '해당 팀의 권한이 없습니다.'}, room=request.sid)
        return
    team_key = 'blue_members' if team == 'blue' else 'red_members'
    if idx < len(room[team_key]):
        room[team_key][idx]['name'] = name
        emit('update_aram', room, room=room_id)

@socketio.on('start_aram_game')
def handle_start_aram_game(data):
    room_id = data['room']
    if room_id not in aram_rooms:
        return
    room = aram_rooms[room_id]
    if not room['blue_members'] or not room['red_members']:
        emit('error', {'msg': '양 팀 모두 최소 1명 이상의 플레이어가 필요합니다.'}, room=request.sid)
        return
    room['game_started'] = True
    emit('aram_game_started', {}, room=room_id)

if __name__ == '__main__':
    import eventlet
    eventlet.monkey_patch()
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
