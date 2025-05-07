from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import random, json, uuid, os

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

# 토너먼트 드래프트 픽 순서 (블루-레드-레드-블루-블루-레드-레드-블루-블루-레드)
BLUE_FIRST_PICK_ORDER = ['blue', 'red', 'red', 'blue', 'blue', 'red', 'red', 'blue', 'blue', 'red']
RED_FIRST_PICK_ORDER = ['red', 'blue', 'blue', 'red', 'red', 'blue', 'blue', 'red', 'red', 'blue']

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
            'first_pick_team': 'blue'  # 첫 게임은 블루팀 첫 픽
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
    
    # 팀 등록
    if team == 'blue' and not rooms[room_id]['blue_team']:
        rooms[room_id]['blue_team'] = request.remote_addr
    elif team == 'red' and not rooms[room_id]['red_team']:
        rooms[room_id]['red_team'] = request.remote_addr
    
    # 현재 픽 순서 결정
    first_pick_team = rooms[room_id]['state']['first_pick_team']
    pick_order = BLUE_FIRST_PICK_ORDER if first_pick_team == 'blue' else RED_FIRST_PICK_ORDER
        
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
    
    # 챔피언 풀 생성
    all_champions = [c['name'] for c in champions]
    random.shuffle(all_champions)
    
    aram_rooms[room_id] = {
        'name': room_name,
        'blue_team': None,
        'red_team': None,
        'blue_members': [],
        'red_members': [],
        'available_champs': all_champions.copy(),
        'reroll_pool_blue': [],  # 블루팀 리롤 풀
        'reroll_pool_red': [],   # 레드팀 리롤 풀
        'all_champs_history': {},  # 모든 챔피언 히스토리 (스왑 추적용)
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
    
    # 팀 등록
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

def generate_random_bans():
    available = [c for c in champions if c['name'] not in previous_picks]
    return random.sample(available, min(10, len(available)))

@socketio.on('join')
def on_join(data):
    room_id = data['room']
    team = data.get('team', 'spectator')
    join_room(room_id)
    emit('status', {'msg': f'{team} 팀이 입장했습니다.'}, room=room_id)
    
    # 현재 방 상태 전송
    if room_id in rooms:
        first_pick_team = rooms[room_id]['state']['first_pick_team']
        emit('update_draft', {
            'blue_picks': rooms[room_id]['state']['blue_picks'],
            'red_picks': rooms[room_id]['state']['red_picks'],
            'current_team': rooms[room_id]['state']['current_team'],
            'pick_phase': rooms[room_id]['state']['pick_phase'],
            'random_bans': [c['name'] for c in rooms[room_id]['state']['random_bans']],
            'first_pick_team': first_pick_team,
            'pick_order': BLUE_FIRST_PICK_ORDER if first_pick_team == 'blue' else RED_FIRST_PICK_ORDER
        }, room=request.sid)
    elif room_id in aram_rooms:
        emit('update_aram', aram_rooms[room_id], room=request.sid)

@socketio.on('pick_champion')
def on_pick(data):
    room_id = data['room']
    team = data['team']
    champion = data['champion']
    
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    current_phase = room['state']['pick_phase']
    first_pick_team = room['state']['first_pick_team']
    pick_order = BLUE_FIRST_PICK_ORDER if first_pick_team == 'blue' else RED_FIRST_PICK_ORDER
    
    # 픽 순서 확인
    if current_phase >= len(pick_order):
        emit('error', {'msg': '모든 픽이 완료되었습니다.'}, room=request.sid)
        return
    
    current_team = pick_order[current_phase]
    
    # 현재 팀 확인
    if current_team != team:
        emit('error', {'msg': '당신의 턴이 아닙니다.'}, room=request.sid)
        return
    
    # 이미 선택된 챔피언인지 확인
    all_selected = (room['state']['blue_picks'] + 
                   room['state']['red_picks'] + 
                   [c['name'] for c in room['state']['random_bans']] +
                   previous_picks)
    
    if champion in all_selected:
        emit('error', {'msg': '이미 선택된 챔피언입니다.'}, room=request.sid)
        return
    
    # 픽 추가
    if team == 'blue':
        room['state']['blue_picks'].append(champion)
    else:
        room['state']['red_picks'].append(champion)
    
    # 픽 단계 증가
    room['state']['pick_phase'] += 1
    
    # 다음 팀 설정
    if room['state']['pick_phase'] < len(pick_order):
        room['state']['current_team'] = pick_order[room['state']['pick_phase']]
    
    # 모든 클라이언트에 업데이트 전송
    emit('update_draft', {
        'blue_picks': room['state']['blue_picks'],
        'red_picks': room['state']['red_picks'],
        'current_team': room['state']['current_team'],
        'pick_phase': room['state']['pick_phase'],
        'random_bans': [c['name'] for c in room['state']['random_bans']],
        'first_pick_team': first_pick_team,
        'pick_order': pick_order
    }, room=room_id)
    
    # 타이머 재시작
    emit('restart_timer', {'duration': 30}, room=room_id)

@socketio.on('finish_game')
def on_finish_game(data):
    room_id = data['room']
    
    if room_id not in rooms:
        return
    
    room = rooms[room_id]
    
    # 전판 픽 목록에 추가
    global previous_picks
    previous_picks.extend(room['state']['blue_picks'])
    previous_picks.extend(room['state']['red_picks'])
    
    # 최대 50개 유지
    if len(previous_picks) > 50:
        previous_picks = previous_picks[-50:]
    
    # 다음 게임은 첫 픽 팀 변경
    next_first_pick = 'red' if room['state']['first_pick_team'] == 'blue' else 'blue'
    
    # 게임 상태 초기화
    room['state'] = {
        'current_team': next_first_pick,  # 다음 게임 첫 픽 팀
        'pick_phase': 0,
        'blue_picks': [],
        'red_picks': [],
        'random_bans': generate_random_bans(),
        'first_pick_team': next_first_pick  # 다음 게임 첫 픽 팀 저장
    }
    
    # 다음 게임 픽 순서
    next_pick_order = BLUE_FIRST_PICK_ORDER if next_first_pick == 'blue' else RED_FIRST_PICK_ORDER
    
    emit('game_finished', {
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
    
    # 전판 픽 초기화
    global previous_picks
    previous_picks = []
    
    # 게임 상태 초기화 (블루팀 첫 픽으로 리셋)
    room['state'] = {
        'current_team': 'blue',
        'pick_phase': 0,
        'blue_picks': [],
        'red_picks': [],
        'random_bans': generate_random_bans(),
        'first_pick_team': 'blue'
    }
    
    emit('game_reset', {
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
    
    # 현재 사용자가 해당 팀의 권한이 있는지 확인
    if (team == 'blue' and room['blue_team'] != request.remote_addr) or \
       (team == 'red' and room['red_team'] != request.remote_addr):
        emit('error', {'msg': '해당 팀의 권한이 없습니다.'}, room=request.sid)
        return
    
    # 멤버 추가
    team_key = 'blue_members' if team == 'blue' else 'red_members'
    
    # 챔피언 랜덤 할당
    if room['available_champs']:
        champ = random.choice(room['available_champs'])
        room['available_champs'].remove(champ)
        
        room[team_key].append({
            'name': nickname,
            'champion': champ,
            'reroll_count': 1
        })
        
        # 챔피언 히스토리 추가
        room['all_champs_history'][champ] = {'team': team, 'original': True}
        
        # 모든 클라이언트에 업데이트 전송
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
    
    # 현재 사용자가 해당 팀의 권한이 있는지 확인
    if (team == 'blue' and room['blue_team'] != request.remote_addr) or \
       (team == 'red' and room['red_team'] != request.remote_addr):
        emit('error', {'msg': '해당 팀의 권한이 없습니다.'}, room=request.sid)
        return
    
    # 멤버 제거
    team_key = 'blue_members' if team == 'blue' else 'red_members'
    
    if idx < len(room[team_key]):
        # 챔피언을 다시 풀에 추가
        member = room[team_key][idx]
        if member['champion']:
            room['available_champs'].append(member['champion'])
            
            # 히스토리에서 제거
            if member['champion'] in room['all_champs_history']:
                del room['all_champs_history'][member['champion']]
        
        # 멤버 제거
        room[team_key].pop(idx)
        
        # 모든 클라이언트에 업데이트 전송
        emit('update_aram', room, room=room_id)

@socketio.on('roll_champion')
def handle_roll_champion(data):
    room_id = data['room']
    team = data['team']
    idx = data['index']
    
    if room_id not in aram_rooms:
        return
    
    room = aram_rooms[room_id]
    
    # 현재 사용자가 해당 팀의 권한이 있는지 확인
    if (team == 'blue' and room['blue_team'] != request.remote_addr) or \
       (team == 'red' and room['red_team'] != request.remote_addr):
        emit('error', {'msg': '해당 팀의 권한이 없습니다.'}, room=request.sid)
        return
    
    # 팀 멤버 목록 선택
    team_key = 'blue_members' if team == 'blue' else 'red_members'
    reroll_pool_key = 'reroll_pool_blue' if team == 'blue' else 'reroll_pool_red'
    
    if idx >= len(room[team_key]):
        emit('error', {'msg': '잘못된 멤버 인덱스입니다.'}, room=request.sid)
        return
    
    member = room[team_key][idx]
    
    # 리롤 횟수 확인
    if member['reroll_count'] <= 0:
        emit('error', {'msg': '리롤 횟수를 모두 사용했습니다.'}, room=request.sid)
        return
    
    # 현재 챔피언을 리롤 풀에 추가
    if member['champion']:
        current_champ = member['champion']
        room[reroll_pool_key].append(current_champ)
        
        # 챔피언 히스토리 업데이트
        room['all_champs_history'][current_champ] = {'team': team, 'original': False}
    
    # 새 챔피언 선택
    if room['available_champs']:
        new_champ = random.choice(room['available_champs'])
        room['available_champs'].remove(new_champ)
        member['champion'] = new_champ
        member['reroll_count'] -= 1
        
        # 새 챔피언 히스토리 추가
        room['all_champs_history'][new_champ] = {'team': team, 'original': True}
    
    # 모든 클라이언트에 업데이트 전송
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
    
    # 현재 사용자가 해당 팀의 권한이 있는지 확인
    if (team == 'blue' and room['blue_team'] != request.remote_addr) or \
       (team == 'red' and room['red_team'] != request.remote_addr):
        emit('error', {'msg': '해당 팀의 권한이 없습니다.'}, room=request.sid)
        return
    
    # 팀 멤버 목록 선택
    team_key = 'blue_members' if team == 'blue' else 'red_members'
    reroll_pool_key = 'reroll_pool_blue' if team == 'blue' else 'reroll_pool_red'
    
    if idx >= len(room[team_key]):
        emit('error', {'msg': '잘못된 멤버 인덱스입니다.'}, room=request.sid)
        return
    
    member = room[team_key][idx]
    
    # 리롤 풀에 있는지 확인
    if champion not in room[reroll_pool_key]:
        emit('error', {'msg': '선택한 챔피언이 리롤 풀에 없습니다.'}, room=request.sid)
        return
    
    # 현재 챔피언을 리롤 풀에 추가 (교환)
    if member['champion']:
        current_champ = member['champion']
        room[reroll_pool_key].append(current_champ)
        
        # 히스토리 업데이트
        room['all_champs_history'][current_champ] = {'team': team, 'original': False}
    
    # 리롤 풀에서 선택한 챔피언으로 변경
    member['champion'] = champion
    room[reroll_pool_key].remove(champion)
    
    # 챔피언 히스토리 업데이트
    room['all_champs_history'][champion] = {'team': team, 'original': False}
    
    # 모든 클라이언트에 업데이트 전송
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
    
    # 현재 사용자가 소스 팀의 권한이 있는지 확인
    if (source_team == 'blue' and room['blue_team'] != request.remote_addr) or \
       (source_team == 'red' and room['red_team'] != request.remote_addr):
        emit('error', {'msg': '해당 팀의 권한이 없습니다.'}, room=request.sid)
        return
    
    # 소스 팀 멤버 목록 선택
    source_team_key = 'blue_members' if source_team == 'blue' else 'red_members'
    target_team_key = 'blue_members' if target_team == 'blue' else 'red_members'
    
    # 인덱스 범위 확인
    if source_idx >= len(room[source_team_key]) or target_idx >= len(room[target_team_key]):
        emit('error', {'msg': '잘못된 멤버 인덱스입니다.'}, room=request.sid)
        return
    
    # 챔피언 교환
    source_member = room[source_team_key][source_idx]
    target_member = room[target_team_key][target_idx]
    
    source_champ = source_member['champion']
    target_champ = target_member['champion']
    
    source_member['champion'] = target_champ
    target_member['champion'] = source_champ
    
    # 챔피언 히스토리 업데이트
    if source_champ in room['all_champs_history']:
        room['all_champs_history'][source_champ]['team'] = target_team
    
    if target_champ in room['all_champs_history']:
        room['all_champs_history'][target_champ]['team'] = source_team
    
    # 모든 클라이언트에 업데이트 전송
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
    
    # 현재 사용자가 해당 팀의 권한이 있는지 확인
    if (team == 'blue' and room['blue_team'] != request.remote_addr) or \
       (team == 'red' and room['red_team'] != request.remote_addr):
        emit('error', {'msg': '해당 팀의 권한이 없습니다.'}, room=request.sid)
        return
    
    # 팀 멤버 목록 선택
    team_key = 'blue_members' if team == 'blue' else 'red_members'
    
    if idx < len(room[team_key]):
        room[team_key][idx]['name'] = name
        
        # 모든 클라이언트에 업데이트 전송
        emit('update_aram', room, room=room_id)

@socketio.on('start_aram_game')
def handle_start_aram_game(data):
    room_id = data['room']
    
    if room_id not in aram_rooms:
        return
    
    room = aram_rooms[room_id]
    
    # 게임 시작 조건 확인 (양 팀 모두 최소 1명 이상)
    if not room['blue_members'] or not room['red_members']:
        emit('error', {'msg': '양 팀 모두 최소 1명 이상의 플레이어가 필요합니다.'}, room=request.sid)
        return
    
    # 게임 시작 상태로 변경
    room['game_started'] = True
    
    # 모든 클라이언트에 게임 시작 알림
    emit('aram_game_started', {}, room=room_id)

if __name__ == '__main__':
    import eventlet
    import eventlet.wsgi
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
