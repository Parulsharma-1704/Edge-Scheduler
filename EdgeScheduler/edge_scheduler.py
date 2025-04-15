import os
import threading
import queue
import time
import random
import sys
from flask import Flask, request, render_template, redirect, url_for
import statistics

# Shared resources
task_queue = queue.Queue()
node_status = {"Node1": True, "Node2": True, "Node3": True}
heartbeat_tracker = {"Node1": time.time(), "Node2": time.time(), "Node3": time.time()}
task_retries = {}
lock = threading.Lock()
tasks_in_progress = {}
total_tasks = 0
task_status = {}
is_running = True
task_logs = []  # Still used for web interface
tasks = []
task_id_counter = 1

# Flask app
app = Flask(__name__)

# Heartbeat updater
def heartbeat_updater(name):
    while True:
        with lock:
            if not node_status[name]:
                break
            heartbeat_tracker[name] = time.time()
        time.sleep(0.1)

# Edge node
def edge_node(name):
    heartbeat_thread = threading.Thread(target=heartbeat_updater, args=(name,), daemon=True)
    heartbeat_thread.start()
    while True:
        with lock:
            if not node_status[name]:
                log_msg = f"{name} has shut down."
                print(log_msg)
                task_logs.append(log_msg)
                break
        try:
            task = task_queue.get(timeout=1)
            task_id, data = task
            with lock:
                tasks_in_progress[task_id] = name
            log_msg = f"{name} received task {task_id}: {data}"
            print(log_msg)
            task_logs.append(log_msg)
            if random.random() < 0.3:
                raise Exception("Processing error")
            sorted_data = sorted(data)
            q1 = statistics.median(sorted_data[:len(sorted_data)//2]) if len(sorted_data) > 1 else sorted_data[0]
            q3 = statistics.median(sorted_data[(len(sorted_data)+1)//2:]) if len(sorted_data) > 1 else sorted_data[0]
            iqr = q3 - q1
            lower_bound = q1 - 0.8 * iqr  # Adjusted to 0.8 for stricter bounds
            upper_bound = q3 + 0.8 * iqr  # Adjusted to 0.8 for stricter bounds
            outliers = [x for x in data if x < lower_bound or x > upper_bound]
            median = statistics.median(data)
            high_cluster = [x for x in data if x > median]
            low_cluster = [x for x in data if x <= median]
            high_avg = statistics.mean(high_cluster) if high_cluster else median
            low_avg = statistics.mean(low_cluster) if low_cluster else median
            outlier_str = f"Outlier{'s' if len(outliers) > 1 else ''} Detected: {', '.join(f'{x:.2f}' for x in outliers)}" if outliers else "No Outliers"
            cluster_str = f"High Avg: {high_avg:.2f}, Low Avg: {low_avg:.2f}"
            result = f"{outlier_str} - {cluster_str}"
            time.sleep(random.uniform(1, 3))
            log_msg = f"{name} completed task {task_id}: {result}"
            print(log_msg)
            task_logs.append(log_msg)
            with lock:
                del tasks_in_progress[task_id]
                task_status[task_id] = ("completed", result)
            task_queue.task_done()
        except queue.Empty:
            time.sleep(0.5)
        except Exception as e:
            with lock:
                retries = task_retries.get(task_id, 0)
                if retries < 3:
                    task_retries[task_id] = retries + 1
                    log_msg = f"{name} failed task {task_id}, retry {retries + 1}/3"
                    print(log_msg)
                    task_logs.append(log_msg)
                    task_status[task_id] = ("retrying", retries + 1)
                    task_queue.put(task)
                else:
                    log_msg = f"Task {task_id} failed after 3 retries"
                    print(log_msg)
                    task_logs.append(log_msg)
                    del tasks_in_progress[task_id]
                    task_status[task_id] = ("failed", None)
                    task_queue.task_done()

# Simulate node failure
def simulate_failure():
    while task_queue.qsize() == 0:
        time.sleep(0.5)
    time.sleep(random.uniform(1, 3))
    with lock:
        alive_nodes = [n for n, alive in node_status.items() if alive]
        if alive_nodes:
            failed_node = random.choice(alive_nodes)
            node_status[failed_node] = False
            log_msg = f"Simulated failure: {failed_node} is down!"
            print(log_msg)
            task_logs.append(log_msg)

# Task scheduler
def scheduler():
    global is_running
    while is_running:
        with lock:
            current_time = time.time()
            for name in list(heartbeat_tracker.keys()):
                if current_time - heartbeat_tracker[name] > 2 and node_status[name]:
                    node_status[name] = False
                    log_msg = f"Scheduler detected {name} failed (no heartbeat)"
                    print(log_msg)
                    task_logs.append(log_msg)
                    for tid, n in list(tasks_in_progress.items()):
                        if n == name and task_retries.get(tid, 0) < 3:
                            task_queue.put(tasks[tid-1])
                            task_retries[tid] = task_retries.get(tid, 0) + 1
                            del tasks_in_progress[tid]
                            log_msg = f"Requeued task {tid} from failed {name}, retry {task_retries[tid]}/3"
                            print(log_msg)
                            task_logs.append(log_msg)
        time.sleep(1)
    while not task_queue.empty() or tasks_in_progress:
        time.sleep(1)
    completed = sum(1 for s, _ in task_status.values() if s == "completed")
    failed = sum(1 for s, _ in task_status.values() if s == "failed")
    retries = sum(task_retries.values())
    summary = f"Summary: Completed: {completed}, Failed: {failed}, Total Retries: {retries}"
    print(summary)
    task_logs.append(summary)
    task_logs.append("All tasks completed or no nodes available.")

# Flask Routes
@app.route('/', methods=['GET', 'POST'])
def index():
    global task_id_counter, total_tasks
    error = None
    if request.method == 'POST':
        user_input = request.form.get('data_input')
        try:
            data = [float(x) for x in user_input.split()]
            if not data:
                error = "Please enter at least one value."
            else:
                task = (task_id_counter, data)
                tasks.append(task)
                task_queue.put(task)
                task_retries[task_id_counter] = 0
                total_tasks += 1
                task_id_counter += 1
                return redirect(url_for('index'))
        except ValueError:
            error = "Invalid input! Use numbers separated by spaces (e.g., '25.0 26.8 24.7')."
    with lock:
        alive = sum(1 for alive in node_status.values() if alive)
        pending = task_queue.qsize()
        in_progress = len(tasks_in_progress)
        completed = sum(1 for s, _ in task_status.values() if s == "completed")
        progress = (completed / total_tasks * 100) if total_tasks > 0 else 0
        status = f"Alive Nodes: {alive}, Pending: {pending}, In Progress: {in_progress}, Progress: {progress:.1f}%"
    return render_template('index.html', tasks=tasks, task_status=task_status, logs=task_logs, status=status, error=error, summary=None)

@app.route('/summary', methods=['POST'])
def summary():
    with lock:
        completed = sum(1 for s, _ in task_status.values() if s == "completed")
        failed = sum(1 for s, _ in task_status.values() if s == "failed")
        retries = sum(task_retries.values())
        summary = f"Summary: Completed: {completed}, Failed: {failed}, Total Retries: {retries}"
        # Log the summary
        task_logs.append(summary)
        # Calculate system status
        alive = sum(1 for alive in node_status.values() if alive)
        pending = task_queue.qsize()
        in_progress = len(tasks_in_progress)
        progress = (completed / total_tasks * 100) if total_tasks > 0 else 0
        status = f"Alive Nodes: {alive}, Pending: {pending}, In Progress: {in_progress}, Progress: {progress:.1f}%"
    return render_template('index.html', tasks=tasks, task_status=task_status, logs=task_logs, status=status, error=None, summary=summary)

def main():
    node_threads = [threading.Thread(target=edge_node, args=(name,), daemon=True) 
                    for name in node_status]
    for t in node_threads:
        t.start()
    threading.Thread(target=simulate_failure, daemon=True).start()
    scheduler_thread = threading.Thread(target=scheduler, daemon=True)
    scheduler_thread.start()
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
