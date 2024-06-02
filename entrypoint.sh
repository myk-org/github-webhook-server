#!/bin/bash

set -ep

if [[ -z $DEVELOPMENT ]]; then
	poetry run uwsgi --disable-logging --post-buffering --master --enable-threads --http 0.0.0.0:5000 --wsgi-file api.py --callable app --processes 4 --threads 2
else
	poetry run python webhook_server_container/app.py
fi
