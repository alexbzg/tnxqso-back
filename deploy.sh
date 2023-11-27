#!/bin/bash
git checkout master
git merge wip
git commit -am "release $1"
cd ../tnxqso
git pull
systemctl stop tnxqso
migrate -path db-migrations -database postgres:///tnxqso?host=/var/run/postgresql/ up
systemctl start tnxqso
