import random
import math
import time
from collections import Counter
import eventlet

def get_line_score(line, is_diag=False):
    counts = Counter(line)
    vals = sorted(line)
    unique_vals = sorted(counts.keys())
    
    # Видаляємо пусті клітинки (None) для підрахунку, якщо раптом потраплять
    if None in vals: return 0

    if counts[1] == 4: return 210 if is_diag else 200
    if set(vals) == {1, 10, 11, 12, 13}: return 160 if is_diag else 150
    if 4 in counts.values(): return 170 if is_diag else 160
    if counts[1] == 3 and counts[13] == 2: return 110 if is_diag else 100
    if 3 in counts.values() and 2 in counts.values(): return 90 if is_diag else 80
    if len(unique_vals) == 5 and (vals[-1] - vals[0] == 4): return 60 if is_diag else 50
    if 3 in counts.values(): return 50 if is_diag else 40
    if list(counts.values()).count(2) == 2: return 30 if is_diag else 20
    if 2 in counts.values(): return 20 if is_diag else 10
    return 0

def calculate_detailed_score(grid_1d):
    # Якщо сітка неповна, заповнюємо нулями для безпеки
    safe_grid = [x if x is not None else 0 for x in grid_1d]
    grid = [safe_grid[i:i+5] for i in range(0, 25, 5)]
    total = 0
    details = {'rows': [], 'cols': [], 'diagonals': []}
    
    for row in grid:
        s = get_line_score(row, False)
        total += s
        details['rows'].append(s)
    for col_idx in range(5):
        col = [grid[row_idx][col_idx] for row_idx in range(5)]
        s = get_line_score(col, False)
        total += s
        details['cols'].append(s)
    d1 = [grid[i][i] for i in range(5)]
    s1 = get_line_score(d1, True)
    total += s1
    details['diagonals'].append(s1)
    
    d2 = [grid[i][4-i] for i in range(5)]
    s2 = get_line_score(d2, True)
    total += s2
    details['diagonals'].append(s2)
    
    return total, details

def _single_ai_run(numbers, duration):
    current_solution = numbers[:]
    random.shuffle(current_solution)
    current_score, _ = calculate_detailed_score(current_solution)
    
    best_sol = current_solution[:]
    best_scr = current_score
    
    start = time.time()
    temp = 100.0
    iterations = 0
    
    while (time.time() - start) < duration:
        iterations += 1
        new_sol = current_solution[:]
        idx1, idx2 = random.sample(range(25), 2)
        new_sol[idx1], new_sol[idx2] = new_sol[idx2], new_sol[idx1]
        
        new_scr, _ = calculate_detailed_score(new_sol)
        
        if new_scr > current_score:
            accept = True
        else:
            delta = new_scr - current_score
            prob = math.exp(delta / temp) if temp > 0.001 else 0
            accept = random.random() < prob
            
        if accept:
            current_solution = new_sol
            current_score = new_scr
            if current_score > best_scr:
                best_scr = current_score
                best_sol = current_solution[:]
        
        temp *= 0.995
        if temp < 0.001: temp = 50.0
        
    return best_sol, best_scr, iterations

def run_smart_ai(numbers, time_limit_sec=5, attempts=4):
    pool = eventlet.GreenPool()
    threads = []
    for _ in range(attempts):
        threads.append(pool.spawn(_single_ai_run, numbers, time_limit_sec))
    
    results = [t.wait() for t in threads]
    
    # Знаходимо найкращий результат
    best_grid, best_score, best_iters = max(results, key=lambda x: x[1])
    
    # Сумуємо ітерації всіх потоків для статистики
    total_iters = sum(r[2] for r in results)
    
    _, details = calculate_detailed_score(best_grid)
    return best_grid, best_score, details, total_iters