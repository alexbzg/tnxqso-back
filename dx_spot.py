#!/usr/bin/python3

import os
import re
#coding=utf-8
from ham_radio import BANDS_WL

APP_PATH = os.path.dirname(os.path.realpath(__file__))
MODES_MAP = []
with open(APP_PATH + '/bandMap.txt', 'r' ) as f_band_map:
    re_band_map = re.compile(r"^(\d+\.?\d*)\s*-?(\d+\.?\d*)\s+(\S+)(\r\n)?$")
    for line in f_band_map.readlines():
        m = re_band_map.match(line)
        if m:
            MODES_MAP.append([m.group(3), float(m.group(1)), float(m.group(2))])

BANDS = [
            ['1.8', 1800, 2000],
            ['3.5', 3500, 4000],
            ['7', 7000, 7300],
            ['10', 1000, 10150],
            ['14', 14000, 14350],
            ['18', 18068, 18168],
            ['21', 21000, 21450],
            ['24', 24890, 24990],
            ['28', 28000, 29700], 
            ['50', 50000, 54000], 
            ['144', 144000, 148000],
            ['UHF', 150000, 2000000]
        ]
MODES = {
            'CW': ('CW', 'A1A'),
            'SSB': ('USB', 'LSB', 'FM', 'SSB', 'AM', 'PHONE'),
            'DIGI': ('DIGI', 'HELL', 'MT63', 'THOR16', 'FAX', 'OPERA', 'PKT',
                'SIM31', 'CONTESTI', 'CONTESTIA', 'AMTOR', 'JT6M', 'ASCI',
                'FT8', 'MSK144', 'THOR', 'QRA64','DOMINO', 'JT4C', 'THROB',
                'DIG', 'ROS', 'SIM63', 'FSQ', 'THRB', 'J3E', 'WSPR', 'ISCAT',
                'CONTESTIA8', 'ALE', 'JT10', 'TOR', 'PACKET', 'RTTY',
                'PSK', 'JT65', 'FSK', 'OLIVIA', 'SSTV',
                'JT9', 'FT8')
        }
SUB_MODES = {'RTTY': None, 'JT65': None, 'PSK': ('PSK31', 'PSK63', 'PSK125')}

def find_diap(diaps, value):
    for diap in diaps:
        if diap[1] <= value <= diap[2]:
            return diap[0]
        elif diap[1] > value:
            return None
    return None

class DX(object):
    def __init__(self, **params):
        self.is_beacon = False
        self.id = params.get('id')
        self.country = params.get('country')
        self.text = params.get('text') or ''
        self.freq = float(params.get('freq'))
        self.cs = params.get('cs')
        if '/QRP' in self.cs:
            self.cs = self.cs.replace( '/QRP', '' )
            self.qrp = True
        else:
            self.qrp = False
        self.de = params.get('de')
        self.lotw = params.get('lotw')
        self.eqsl = params.get('eqsl')
        self.time = params.get('time')
        self.dxcc = params.get('dxcc')

        txt = self.text.lower()
        if 'ncdxf' in txt or 'beacon' in txt or 'bcn' in txt or '/B' in self.cs:
            self.is_beacon = True
            return
        
        band = params.get('band')
        if band in BANDS_WL:
            band = BANDS_WL[band]
        self.band = band
        self.mode = None
        self.sub_mode = None
        if params.get('mode'):
            self.setMode(params['mode'])
            if not self.mode:
                self.mode = params['mode']
        else:
            self.mode = None

        if not self.band and self.freq:
            self.band = find_diap(BANDS, self.freq)
            if not self.band:
                return

        if not self.mode and self.text:
            t = self.text.upper()
            for (mode, aliases) in MODES.items():
                for alias in aliases:
                    if re.search('(^|\s)' + alias + '(\d|\s|$)', t):
                        self.set_mode(alias)
                        break
        if not self.mode and self.freq:
            mode_by_map = find_diap(MODES_MAP, self.freq)
            if mode_by_map:
                if mode_by_map == 'BCN':
                    self.is_beacon = True
                    return
                self.set_mode(mode_by_map)

        slash_pos = self.cs.find( '/' )
        if self.cs.endswith('/AM') or self.cs.endswith('/MM') or self.sub_mode == 'PSK125':
            return
    
    def set_mode( self, value ):
        alias = None
        for (mode, aliases) in MODES.items():
            if value == mode:
                self.mode = mode
                break
            if value in aliases:
                self.mode = mode
                alias = value
                break
            for a in aliases:
                if a in value:
                    self.mode = mode
                    alias = a
                    break
            if alias:
                break
        if alias in SUB_MODES:
            if SUB_MODES[alias]:
                t = self.text.upper()
                for sub_mode in SUB_MODES[alias]:
                    if sub_mode in t:
                        self.sub_mode = sub_mode
                        break
            else:
                self.sub_mode = alias
            if not self.sub_mode:
                for a in SUB_MODES[alias]:
                    if a in value:
                        self.sub_mode = a
                        break
            if not self.sub_mode:
                self.sub_mode = SUB_MODES[alias][0]


    def toDict(self):
        if self.is_beacon:
            return { 'beacon': True }
        return {
            'id': self.id,
            'dxcc': self.dxcc,
            'cs': self.cs,
            'qrp': self.qrp,
            'text': self.text,
            'de': self.de,
            'freq': self.freq,
            'dt': self.time,
            'time': self.time[11:16],
            'country' : self.country,
            'mode': self.mode,
            'subMode': self.sub_mode,
            'band': self.band,
            'lotw': self.lotw,
            'eqsl': self.eqsl
            }


