#!/bin/zsh
cd "$(dirname "$0")"
python3 tracker.py --export-only
printf "\n按回车关闭窗口"
read
