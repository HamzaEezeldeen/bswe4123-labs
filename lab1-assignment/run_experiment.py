"""Reproduces the 60-second experiment with real processes.

Timeline (seconds after the monitor starts):
    t= 8  stop the service on 8003      (process failure)
    t=20  restart 8003                  (recovery)
    t=30  restart 8002 with 0.8 s delay (slow service)
    t=44  restart 8002 normally         (back to normal)
The monitor output is printed and saved to experiment_output.txt.
"""
import subprocess
import sys
import time

import httpx


def start(port, delay=None):
    cmd = [sys.executable, "service.py", "--port", str(port)]
    if delay is not None:
        cmd += ["--delay", str(delay)]
    return subprocess.Popen(cmd)


def wait_ready(port):
    for _ in range(50):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.2)
    raise RuntimeError(f"service on {port} did not start")


def stop(proc):
    proc.terminate()
    proc.wait()


services = {p: start(p) for p in (8001, 8002, 8003)}
for p in services:
    wait_ready(p)

monitor = subprocess.Popen([sys.executable, "monitor.py", "--duration", "60"],
                           stdout=subprocess.PIPE, text=True)
t0 = time.monotonic()


def at(seconds):
    time.sleep(max(0, t0 + seconds - time.monotonic()))


try:
    at(8);  print(">> stopping 8003", file=sys.stderr);  stop(services[8003])
    at(20); print(">> restarting 8003", file=sys.stderr); services[8003] = start(8003)
    at(30); print(">> restarting 8002 with 0.8 s delay", file=sys.stderr)
    stop(services[8002]); services[8002] = start(8002, delay=0.8)
    at(44); print(">> restarting 8002 normally", file=sys.stderr)
    stop(services[8002]); services[8002] = start(8002)
    output = monitor.communicate()[0]
finally:
    for proc in services.values():
        if proc.poll() is None:
            stop(proc)

print(output)
with open("experiment_output.txt", "w") as f:
    f.write(output)
