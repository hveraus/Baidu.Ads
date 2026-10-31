#!/bin/zsh
cd "$(dirname "$0")"
report_root="$PWD"
if ! /usr/bin/curl --silent --fail --max-time 2 http://127.0.0.1:8796/ -o /dev/null; then
  nohup python3 -m http.server 8796 --bind 127.0.0.1 --directory "$report_root/report" </dev/null >>"$report_root/report-server.log" 2>&1 &
  for report_attempt in {1..20}; do
    /usr/bin/curl --silent --fail --max-time 1 http://127.0.0.1:8796/ -o /dev/null && break
    /bin/sleep 0.2
  done
fi
/usr/bin/open "http://127.0.0.1:8796/"
