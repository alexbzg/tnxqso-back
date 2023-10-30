##!/usr/bin/python3
#coding=utf-8
import reverse_geocoder as rg

CORRECT_COUNTRIES = {
    'UA': {
        'admin1': ('Crimea', 'Misto Sevastopol\''),
        'value': 'RU'
        }
    }

def get_country(location):
    data = rg.search(location)
    country = None
    if data:
        data = data[0]
        country = data['cc']
        if country in CORRECT_COUNTRIES and data['admin1'] in\
            CORRECT_COUNTRIES[country]['admin1']:
            country = CORRECT_COUNTRIES[country]['value']
    return country
