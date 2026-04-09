#!/bin/bash
pkill -f "python3 app.py" 2>/dev/null
sleep 1
cd ~/dashboard
nohup python3 app.py > dashboard.log 2>&1 &
echo $! > dashboard.pid
echo "대시보드 시작 (PID: $!)"
