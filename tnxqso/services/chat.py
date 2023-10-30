#!/usr/bin/python3
#coding=utf-8
import time
import json
from datetime import datetime

from tnxqso.common import CONF, loadJSON, dtFmt

CHAT_MAX_LENGTH = int(CONF['chat']['max_length'])

def insert_chat_message(path, msg_data, admin):
    chat = loadJSON(path) or []
    msg = {'user': msg_data['from'],
            'text': msg_data['text'],
            'cs': msg_data.get('cs') or msg_data['from'],
            'admin': admin, 'ts': time.time()}
    msg['date'], msg['time'] = dtFmt(datetime.utcnow())
    if 'name' in msg_data:
        msg['name'] = msg_data['name']
    chat.insert(0, msg)
    chat_trunc = []
    chat_adm = []
    for msg in chat:
        if msg['text'].startswith('***') and msg['admin']:
            chat_adm.append(msg)
        elif len(chat_trunc) < CHAT_MAX_LENGTH:
            chat_trunc.append(msg)
    chat = chat_adm + chat_trunc
    with open(path, 'w') as f_chat:
        json.dump(chat, f_chat, ensure_ascii = False)
