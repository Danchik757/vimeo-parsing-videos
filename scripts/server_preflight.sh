#!/bin/bash
set -euo pipefail

echo "== basic =="
date
whoami
id
pwd
uname -a
if [[ -f /etc/os-release ]]; then
  cat /etc/os-release
fi

echo
echo "== privileges =="
if command -v sudo >/dev/null 2>&1; then
  if sudo -n true 2>/dev/null; then
    echo "sudo: passwordless available"
  else
    echo "sudo: installed, password may be required"
  fi
else
  echo "sudo: not installed"
fi

echo
echo "== tools =="
python3 --version || true
pip3 --version || true
git --version || true
tmux -V || true
screen --version || true
curl --version | head -n 1 || true
wget --version | head -n 1 || true

echo
echo "== browser =="
google-chrome --version || chromium --version || chromium-browser --version || true
xvfb-run --help >/dev/null 2>&1 && echo "xvfb-run: ok" || echo "xvfb-run: missing"

echo
echo "== resources =="
df -h
free -h || true
nproc || true

echo
echo "== writable paths =="
for target in "$HOME" "/tmp" "/srv" "/opt"; do
  if [[ -e "$target" ]]; then
    if [[ -w "$target" ]]; then
      echo "$target : writable"
    else
      echo "$target : not writable"
    fi
  else
    echo "$target : missing"
  fi
done

echo
echo "== network =="
if command -v curl >/dev/null 2>&1; then
  curl -I --max-time 10 https://vimeo.com >/dev/null && echo "vimeo.com: ok" || echo "vimeo.com: failed"
  curl -I --max-time 10 https://api.vimeo.com >/dev/null && echo "api.vimeo.com: ok" || echo "api.vimeo.com: failed"
  curl -I --max-time 10 https://api.telegram.org >/dev/null && echo "api.telegram.org: ok" || echo "api.telegram.org: failed"
fi
