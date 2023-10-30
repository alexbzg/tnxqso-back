#!/usr/bin/python
#coding=utf-8

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import smtplib

from tnxqso.common import CONF

def send_email(**email):
    my_address = CONF.get('email', 'address')
    msg = MIMEMultipart()
    msg.attach( MIMEText(email['text'].encode('utf-8'), 'plain', 'UTF-8'))
    msg['Reply-To'] = email['fr']
    msg['to'] = email['to']
    msg['MIME-Version'] = "1.0"
    msg['Subject'] = email['subject']
    msg['Content-Type'] = "text/plain; charset=utf-8"
    msg['Content-Transfer-Encoding'] = "quoted-printable"

    if email.get('attachments'):
        for item in email['attachments']:
            part = MIMEApplication(item['data'],
                        Name = item['name'])
            part['Content-Disposition'] = f'attachment; filename="{item["name"]}"'
            msg.attach(part)
    server = smtplib.SMTP_SSL(CONF.get('email', 'smtp'))
    server.login(CONF.get('email', 'login'), CONF.get('email', 'password'))
    server.sendmail(my_address, msg['to'], str(msg))
