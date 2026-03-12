import psutil
import datetime
import os

# Function to log running processes
def log_processes():
    log_file = 'process_log.txt'
    with open(log_file, 'a') as f:
        f.write(f'Process Log at {datetime.datetime.now()}\n')
        f.write('----------------------------------------\n')
        for proc in psutil.process_iter(['name', 'pid', 'cpu_percent', 'memory_info']):
            try:
                info = proc.as_dict(attrs=['name', 'pid', 'cpu_percent', 'memory_info'])
                f.write(f"Name: {info['name']}, PID: {info['pid']}, CPU: {info['cpu_percent']}%, Memory: {info['memory_info'].rss / 1024 / 1024:.2f} MB\n")
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        f.write('\n')
    print(f'Process list logged to {log_file}')

if __name__ == '__main__':
    log_processes()
