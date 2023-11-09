#!/usr/bin/python3
#coding=utf-8

import logging
import traceback
import json

import aiopg

from tnxqso.common import CONF

async def to_dict(cur, container=None, key_column='id'):
    if not cur or not cur.rowcount:
        return False

    col_names = [col.name for col in cur.description]
    if cur.rowcount == 1 and not container:
        data = await cur.fetchone()
        return dict(zip(col_names, data))

    data = await cur.fetchall()
    if key_column and (key_column in col_names) and container == 'dict':
        id_idx = col_names.index(key_column)
        return {row[ id_idx ]: dict(zip(col_names, row)) for row in data}

    return [dict(zip(col_names, row)) for row in data]

def param_str(params, delim):
    return delim.join([f"{x} = %({x})s" for x in params.keys()])

def splice_params(data, params):
    return {param: json.dumps(data[param])
            if isinstance(data[param],dict) else data[param]
        for param in params
        if param in data}

async def init_connection(_conn):
    logging.debug('new db connection')

class DBConn:

    def __init__(self, db_params):
        self.dsn = ' '.join([f"{k}='{v}'" for k, v in db_params])
        self.verbose = False
        self.pool = None
        self.error = None

    async def connect(self):
        try:
            self.pool = await aiopg.create_pool(self.dsn, maxsize=3,
                    on_connect = init_connection)
            logging.debug('db connections pool is created')
        except:
            logging.exception('Error creating connection pool')
            logging.error(self.dsn)

    async def disconnect(self):
        self.pool.close()
        logging.debug('closing db connections pool')
        await self.pool.wait_closed()
        logging.debug('db connections pool was closed')

    async def param_update(self, table, id_params, upd_params):
        return await self.execute(f"""
                update {table}
                set {param_str(upd_params, ', ')}
                where {param_str(id_params, ' and ')}""",
                dict(id_params, **upd_params))

    async def param_delete(self, table, id_params):
        return await self.execute(f"""
                delete from {table}
                where {param_str(id_params, ' and ')}""",
                id_params)

    async def param_upsert(self, table, id_params, upd_params):
        lookup = await self.get_object(table, id_params, False, True)
        res = None
        if lookup:
            res = await self.param_update(table, id_params, upd_params)
        else:
            res = await self.get_object(table, dict(id_params, **upd_params),
                    True)
        return res

    async def execute(self, sql, params=None, container=None, key_column=None):
        res = False
        with (await self.pool.cursor()) as cur:
            try:
                if self.verbose:
                    logging.debug(sql)
                    logging.debug(params)
                await cur.execute(sql, params)
                res = (await to_dict(cur, container, key_column)
                        if cur.description is not None else True)
            except Exception as exc:
                logging.exception("Error executing: %s", sql)
                stack = traceback.extract_stack()
                logging.error(stack)
                if params:
                    logging.error("Params: %s", params)
                if hasattr(exc, 'pgerror'):
                    logging.error(exc.pgerror)
                    self.error = exc.pgerror
        return res

    async def get_station_callsign(self, admin_cs):
        data = await self.get_user_data(admin_cs)
        return data['settings']['station']['callsign']

    async def get_user_data(self, callsign):
        user_data = await self.get_object('users', {'callsign': callsign}, False, True)
        if user_data:
            user_data['banned_by'] = (await self.execute("""
                select array_agg(admin_callsign) as admins 
                from user_bans join users on banned_callsign = callsign
                where email = (select email from users as u1 where u1.callsign = %(callsign)s);
                """, {'callsign': callsign})).get('admins', [])
        return user_data

    async def get_object(self, table, params, create=False, never_create=False):
        sql = ''
        res = False
        if not create:
            where_clause = " and ".join([f"{k} = %({k})s"
                        if params[k] is not None
                        else f"{k} is null"
                        for k in params.keys()])
            sql = f"""
                select * from {table} 
                where {where_clause}"""
            res = await self.execute(sql, params)
        if create or (not res and not never_create):
            keys = ", ".join(params.keys())
            values = ", ".join([f"%({key})s" for key in params.keys()])
            sql = f"""
                insert into {table} 
                ({keys})
                values ({values})
                returning *"""
            logging.debug('creating object in db')
            res = await self.execute(sql, params)
        return res

    async def update_object(self, table, update_params, id_param = "id"):
        update_params_string = param_str(update_params,  ", ")
        if update_params_string:
            sql = f"""
                update {table} 
                set {update_params_string}
                where {id_param} = %({id_param})s returning *"""
            with (await self.execute(sql, update_params)) as cur:
                if cur:
                    obj_res = await to_dict(cur)
                    return obj_res

    async def delete_object(self, table, el_id):
        await self.execute(f"delete from {table} where id = %s", (el_id,))

DB = DBConn(CONF.items('db'))
