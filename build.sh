#!/bin/sh
set -e
cd "$(dirname "$0")"
v=$(python3 -c 'import json;print(json.load(open("asw/plugin.json"))["version"])')
mkdir -p dist
rm -f "dist/avnav-asw-$v.zip"
zip -qr "dist/avnav-asw-$v.zip" asw -x '*/__pycache__/*' '*.pyc'
echo "dist/avnav-asw-$v.zip"
