#!/bin/bash
cd ../tnxqso
git pull
systemctl stop tnxqso
migrate -path db-migrations -database postgres:///tnxqso?host=/var/run/postgresql/ up
systemctl start tnxqso
