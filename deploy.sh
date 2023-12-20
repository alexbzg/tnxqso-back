#!/bin/bash
cd ../tnxqso
git pull
systemctl stop tnxqso
su -c "migrate -path db-migrations -database postgres:///tnxqso?host=/var/run/postgresql/ up" postgres
systemctl start tnxqso
