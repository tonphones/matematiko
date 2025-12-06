from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
import random
import string
import time
from collections import Counter
from ai_solver import calculate_detailed_score, run_smart_ai
import eventlet

app = Flask(__name__)
app.config['SECRET_KEY'] = 'matematico_secret_key'
socketio = SocketIO(app, async_mode='eventlet')

rooms = {}

# --- КОНФІГУРАЦІЯ ---
AI_THINK_TIME = 60    
TIMER_TURN_CLASSIC = 5
TIMER_END_FREE = 10 
TIMER_GUESSING_FINAL = 10 

def generate_room_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def create_deck():
    deck = []
    for i in range(1, 14):
        deck.extend([i] * 4) 
    random.shuffle(deck)
    return deck

@app.route('/')
def index():
    return render_template('index.html')

# --- SOCKETS ---

@socketio.on('create_game')
def on_create(data):
    if data.get('password') != "M@t#m@t1k0":
        emit('error', {'msg': 'Невірний пароль!'})
        return

    room_code = f"{generate_room_code()[:3]}-{generate_room_code()[3:]}"
    rooms[room_code] = {
        'host_sid': request.sid,
        'mode': data.get('mode'),
        'players': {},
        'deck': create_deck(),
        'history_drawn': [], 
        'current_drawn': [], 
        'free_mode_numbers': [],
        'state': 'LOBBY',
        'current_number': None,
        'round_count': 0,
        'round_history': [], 
        'correct_missing_nums': [],
        'processing_turn': False
    }
    join_room(room_code)
    emit('game_created', {'room_code': room_code, 'mode': data.get('mode')})

@socketio.on('join_game')
def on_join(data):
    room = data['room']
    name = data['name']
    avatar = data['avatar']
    if room not in rooms: return
    if rooms[room]['host_sid'] == request.sid: return
    
    join_room(room)
    rooms[room]['players'][request.sid] = {
        'name': name, 'avatar': avatar, 
        'grid': [None]*25, 
        'prev_grid': None, 
        'ready_turn': False, 
        'sid': request.sid,
        'guesses': [],
        'score': 0,
        'last_turn_index': None 
    }
    update_player_list(room)
    emit('joined_successfully', {'room': room, 'name': name, 'mode': rooms[room]['mode']})

@socketio.on('kick_player')
def on_kick(data):
    room = data['room']
    if room in rooms and rooms[room]['host_sid'] == request.sid:
        target_sid = data['sid']
        if target_sid in rooms[room]['players']:
            emit('kicked', room=target_sid)
            del rooms[room]['players'][target_sid]
            disconnect(target_sid)
            update_player_list(room)

def update_player_list(room):
    plist = [{'name': p['name'], 'avatar': p['avatar'], 'ready': p['ready_turn'], 'sid': p['sid']} 
             for p in rooms[room]['players'].values()]
    emit('update_players', {'players': plist}, room=room)

@socketio.on('start_game')
def on_start(data):
    room = data['room']
    game = rooms.get(room)
    if not game or game['host_sid'] != request.sid: return

    if not game['players']:
        emit('display_error', {'msg': 'Немає гравців! Чекаємо...'}, room=request.sid)
        return

    if game['state'] == 'LOBBY':
        game['history_drawn'] = []
        game['current_drawn'] = []
        game['round_history'] = []
        for p in game['players'].values():
            p['prev_grid'] = None
            p['score'] = 0
            p['last_turn_index'] = None

    game['state'] = 'PLAYING'
    game['processing_turn'] = False
    emit('game_started', {'mode': game['mode']}, room=room)
    send_host_info(room)

    for pid, p in game['players'].items():
        if p['prev_grid']:
            emit('set_prev_grid', {'grid': p['prev_grid']}, room=pid)

    if game['mode'] == 'classic': next_turn_classic(room)
    else: start_free_mode(room)

def send_host_info(room):
    game = rooms[room]
    next_25 = game['deck'][-25:] if len(game['deck']) >= 25 else game['deck'][:]
    emit('host_deck_info', {'history': game['history_drawn'], 'current': list(reversed(next_25))}, room=game['host_sid'])

# --- CLASSIC ---
def next_turn_classic(room_code):
    game = rooms[room_code]
    game['processing_turn'] = False 

    if game['round_count'] >= 25:
        handle_round_end(room_code)
        return

    if not game['deck']: game['deck'] = create_deck()
    num = game['deck'].pop()
    game['current_drawn'].append(num) 
    game['current_number'] = num
    game['round_count'] += 1
    
    for pid in game['players']: 
        game['players'][pid]['ready_turn'] = False
        game['players'][pid]['last_turn_index'] = None

    update_player_list(room_code)
    emit('new_number_classic', {'number': num, 'round': game['round_count']}, room=room_code)

@socketio.on('place_number_classic')
def on_place_classic(data):
    room = data['room']
    game = rooms.get(room)
    if not game or game['state'] != 'PLAYING': return
    
    player = game['players'].get(request.sid)
    if player and game['current_number'] is not None:
        idx = data['index']
        
        if idx == player['last_turn_index']:
             player['grid'][idx] = None
             player['last_turn_index'] = None
             player['ready_turn'] = False
             emit('move_confirmed', {'grid': player['grid']})
             update_player_list(room)
             return

        if player['grid'][idx] is None:
            if player['last_turn_index'] is not None:
                old_idx = player['last_turn_index']
                player['grid'][old_idx] = None
            
            player['grid'][idx] = game['current_number']
            player['last_turn_index'] = idx
            player['ready_turn'] = True
            
            emit('move_confirmed', {'grid': player['grid']})
            update_player_list(room)

@socketio.on('host_force_next')
def host_force_next(data):
    room = data['room']
    game = rooms.get(room)
    if not game or game['host_sid'] != request.sid: return
    
    if game['processing_turn']: return
    game['processing_turn'] = True

    wait_time = 5 if game['round_count'] >= 25 else TIMER_TURN_CLASSIC
    # NEW: Send absolute timestamp
    end_time = time.time() + wait_time
    emit('start_real_timer', {'seconds': wait_time, 'endTime': end_time, 'msg': 'Залишилося часу на хід:'}, room=room)
    socketio.sleep(wait_time)
    
    for p in game['players'].values():
        if not p['ready_turn']:
            empties = [i for i, v in enumerate(p['grid']) if v is None]
            if empties:
                chosen = random.choice(empties)
                p['grid'][chosen] = game['current_number']
                p['ready_turn'] = True
                emit('force_update_grid', {'grid': p['grid']}, room=p['sid'])
    next_turn_classic(room)

# --- FREE MODE ---
def start_free_mode(room_code):
    game = rooms[room_code]
    game['processing_turn'] = False
    if len(game['deck']) < 25: game['deck'] = create_deck()
    nums = [game['deck'].pop() for _ in range(25)]
    game['current_drawn'] = nums 
    game['free_mode_numbers'] = sorted(nums)
    emit('start_free_mode_client', {'numbers': game['free_mode_numbers']}, room=room_code)

@socketio.on('update_grid_free')
def update_grid_free(data):
    room = data['room']
    game = rooms.get(room)
    if game and game['state'] == 'PLAYING':
        game['players'][request.sid]['grid'] = data['grid']

@socketio.on('host_end_free_game')
def host_end_free_game(data):
    room = data['room']
    game = rooms.get(room)
    if not game or game['host_sid'] != request.sid: return
    
    if game['processing_turn']: return
    game['processing_turn'] = True
    
    try:
        emit('start_real_timer', {'seconds': TIMER_END_FREE, 'msg': 'Залишилося щоб заповнити таблицю:'}, room=room)
        socketio.sleep(TIMER_END_FREE)
        
        # Просимо клієнтів надіслати фінальний стан (страховка)
        emit('request_final_grid', {}, room=room)
        socketio.sleep(2)
        
        # Гарантоване заповнення порожніх клітинок
        for p in game['players'].values():
            fill_free_mode_grid(p, game['free_mode_numbers'])
            
        handle_round_end(room)
    except Exception as e:
        print(f"CRITICAL ERROR in free game: {e}")
        # У разі помилки знімаємо блокування, щоб можна було натиснути ще раз
        game['processing_turn'] = False

def fill_free_mode_grid(player, available_numbers):
    current_grid = player['grid']
    
    # КРОК 1: Очищаємо та конвертуємо все в числа (фікс помилок типів)
    safe_grid = []
    for x in current_grid:
        try:
            # Якщо там щось є, робимо int, якщо ні - None
            val = int(x) if (x is not None and x != "") else None
            safe_grid.append(val)
        except:
            safe_grid.append(None)
    
    # КРОК 2: Рахуємо, що вже стоїть
    placed_counts = Counter([x for x in safe_grid if x is not None])
    total_counts = Counter(available_numbers)
    
    # КРОК 3: Формуємо пул чисел, яких не вистачає
    pool = []
    for num, count in total_counts.items():
        rem = count - placed_counts.get(num, 0)
        if rem > 0:
            pool.extend([num] * rem)
    
    random.shuffle(pool)
    
    # КРОК 4: Заповнюємо пропуски
    final_grid = []
    for cell in safe_grid:
        if cell is None:
            # Якщо пул пустий (дивна помилка), ставимо 0, інакше беремо з пулу
            val = pool.pop() if pool else 0
            final_grid.append(val)
        else:
            final_grid.append(cell)
            
    player['grid'] = final_grid

def fill_free_mode_grid(player, available_numbers):
    current_grid = player['grid']
    placed_counts = Counter([x for x in current_grid if x is not None])
    total_counts = Counter(available_numbers)
    
    pool = []
    for num, count in total_counts.items():
        rem = count - placed_counts[num]
        if rem > 0: pool.extend([num] * rem)
    
    random.shuffle(pool)
    
    new_grid = []
    for cell in current_grid:
        if cell is None:
            val = pool.pop() if pool else 0
            new_grid.append(val)
        else:
            new_grid.append(cell)
    player['grid'] = new_grid

# --- END ROUND & GUESSING ---
def handle_round_end(room_code):
    game = rooms[room_code]
    game['processing_turn'] = False
    
    if len(game['deck']) == 2:
        game['state'] = 'GUESSING'
        game['correct_missing_nums'] = game['deck'][:] 
        emit('start_guessing_wait_host', {}, room=room_code)
        
        for pid, p in game['players'].items():
            emit('show_guessing_grids', {
                'grid1': p['prev_grid'], 
                'grid2': p['grid']       
            }, room=pid)
    else:
        calculate_results(room_code, guessing_happened=False)

@socketio.on('host_trigger_guessing')
def host_trigger_guessing(data):
    room = data['room']
    game = rooms.get(room)
    if not game or game['host_sid'] != request.sid: return
    
    # NEW: Send absolute timestamp
    end_time = time.time() + TIMER_GUESSING_FINAL
    emit('start_guessing_timer', {'seconds': TIMER_GUESSING_FINAL, 'endTime': end_time}, room=room)
    
    socketio.sleep(TIMER_GUESSING_FINAL)
    calculate_results(room, guessing_happened=True)

@socketio.on('submit_guesses')
def on_submit_guesses(data):
    room = data['room']
    try:
        g1 = int(data.get('g1', 0))
        g2 = int(data.get('g2', 0))
    except:
        g1, g2 = 0, 0
    game = rooms.get(room)
    if game and request.sid in game['players']:
        game['players'][request.sid]['guesses'] = [g1, g2]

# --- RESULTS ---
def calculate_results(room_code, guessing_happened=False):
    game = rooms[room_code]
    game['state'] = 'CALCULATING'
    game['history_drawn'].extend(game['current_drawn'])
    
    emit('game_finished_wait', {'seconds': AI_THINK_TIME}, room=room_code)
    socketio.sleep(AI_THINK_TIME)
    
    used_nums = game['current_drawn'] if game['mode'] == 'classic' else game['free_mode_numbers']
    correct_missing = game['correct_missing_nums'] if guessing_happened else []
    
    ai_guesses = correct_missing[:] if guessing_happened else []
    
    ai_grid, ai_score, ai_details, ai_iters = run_smart_ai(used_nums, time_limit_sec=3)
    current_round_results = []

    for sid, p in game['players'].items():
        score, details = calculate_detailed_score(p['grid'])
        g_bonus = 0 
        g_res = []
        
        if guessing_happened:
            user_guesses = p.get('guesses', [])
            if not user_guesses: user_guesses = random.sample(range(1, 14), 2)
            p['guesses'] = user_guesses
            
            actual_counts = Counter(correct_missing)
            
            for g in user_guesses:
                is_correct = False
                if actual_counts[g] > 0:
                    is_correct = True
                    actual_counts[g] -= 1
                g_res.append({'val': g, 'ok': is_correct})
        else:
            p['guesses'] = []
        
        p['prev_grid'] = p['grid'][:]

        current_round_results.append({
            'sid': sid, 'type': 'human', 'name': p['name'], 'avatar': p['avatar'],
            'grid_score': score, 'bonus': g_bonus, 'total_score': score + g_bonus,
            'grid': p['grid'], 'details': details, 'guesses': g_res
        })

    ai_g_res = [{'val': g, 'ok': True} for g in ai_guesses]
    current_round_results.append({
        'sid': 'ai', 'type': 'ai', 'name': 'Super AI', 'avatar': 'ai_bot',
        'grid_score': ai_score, 'bonus': 0, 'total_score': ai_score,
        'grid': ai_grid, 'details': ai_details, 'iterations': ai_iters, 'guesses': ai_g_res
    })

    current_round_results.sort(key=lambda x: x['total_score'], reverse=True)
    assign_rank(current_round_results)

    round_idx = len(game['round_history']) + 1
    game['round_history'].append({'id': round_idx, 'results': current_round_results})

    total_map = {}
    total_map['ai'] = {'sid': 'ai', 'name': 'Super AI', 'avatar': 'ai_bot', 'total_score': 0, 'rounds': {}, 'type': 'ai'}
    for sid, p in game['players'].items():
        total_map[sid] = {'sid': sid, 'name': p['name'], 'avatar': p['avatar'], 'total_score': 0, 'rounds': {}, 'type': 'human'}

    for r_data in game['round_history']:
        rid = r_data['id']
        for res in r_data['results']:
            sid = res['sid']
            if sid not in total_map:
                 total_map[sid] = {'sid': sid, 'name': res['name'], 'avatar': res['avatar'], 'total_score': 0, 'rounds': {}, 'type': res['type']}
            total_map[sid]['total_score'] += res['total_score']
            total_map[sid]['rounds'][rid] = res

    total_results = list(total_map.values())
    total_results.sort(key=lambda x: x['total_score'], reverse=True)
    assign_rank(total_results)

    can_continue = (len(game['deck']) >= 25)

    emit('show_results', {
        'round_results': current_round_results,
        'total_results': total_results,
        'round_id': round_idx,
        'correct_missing': correct_missing,
        'can_continue': can_continue
    }, room=room_code)

def assign_rank(items):
    rank = 1
    for i in range(len(items)):
        if i > 0 and items[i]['total_score'] < items[i-1]['total_score']:
            rank = i + 1
        items[i]['rank'] = rank

@socketio.on('next_round_action')
def next_round_action(data):
    room = data['room']
    action = data['action']
    game = rooms.get(room)
    if not game or game['host_sid'] != request.sid: return

    game['processing_turn'] = False 

    if action == 'finish':
        game['deck'] = create_deck()
        game['round_count'] = 0
        game['state'] = 'LOBBY'
        game['history_drawn'] = []
        game['current_drawn'] = []
        game['round_history'] = [] 
        game['players'] = {} 
        emit('return_to_landing', {}, room=room)
        return

    for p in game['players'].values():
        p['grid'] = [None] * 25
        p['ready_turn'] = False
        p['last_turn_index'] = None
        p['guesses'] = []
    
    game['round_count'] = 0
    game['state'] = 'PLAYING'
    game['current_drawn'] = [] 
    game['correct_missing_nums'] = []
    
    if action == 'restart':
        game['deck'] = create_deck()
        game['history_drawn'] = [] 
        game['round_history'] = [] 
        for p in game['players'].values(): 
            p['prev_grid'] = None
            p['score'] = 0

    emit('reset_client', {'has_prev': (action=='continue')}, room=room)
    
    if action == 'continue':
        for pid, p in game['players'].items():
            if p['prev_grid']:
                emit('set_prev_grid', {'grid': p['prev_grid']}, room=pid)

    send_host_info(room)
    
    if game['mode'] == 'classic': next_turn_classic(room)
    else: start_free_mode(room)

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000, host='0.0.0.0')
