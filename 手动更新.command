#!/bin/zsh
cd "$(dirname "$0")"
python3 tracker.py
printf "\n按回车关闭窗口"
read
