#!/usr/bin/python3
#coding=utf-8
import os
from ctypes import c_void_p, c_size_t
import uuid

import ffmpeg
from wand.image import Image
from wand.color import Color
from wand.api import library
from aiohttp import web

from tnxqso.common import CONF
from tnxqso.db import DB
from tnxqso.services.auth import auth, extract_callsign, SITE_ADMINS
from tnxqso.services.station_dir import (get_station_path_by_admin_cs, delete_blog_entry,
    get_gallery_size)

library.MagickSetCompressionQuality.argtypes = [c_void_p, c_size_t]

BLOG_ROUTES = web.RouteTableDef()

@BLOG_ROUTES.get('/aiohttp/blog/{callsign}')
async def get_blog_entries_handler(request):
    callsign = extract_callsign(request)
    data = await DB.execute("""
            select id, "file", file_type, file_thumb, txt, 
                to_char(timestamp_created, 'DD Mon YYYY HH24:MI') as post_datetime,
                extract(epoch from timestamp_created) as ts,
                (select count(*) 
                    from blog_reactions 
                    where entry_id = blog_entries.id) as reactions,
                (select blog_comments.id
                    from blog_comments
                    where entry_id = blog_entries.id
                    order by blog_comments.id desc
                    limit 1) as last_comment_id
            from blog_entries
            where "user" = %(callsign)s
            order by id desc
            """,
            params={'callsign': callsign},
            container='list')
    if not data:
        return web.HTTPNotFound(text='Blog entries not found')
    last_modified = (await DB.execute("""
        select to_char(greatest(
            (select timestamp_created 
            from blog_entries
            where "user" = callsign
            order by id desc limit 1), 
            last_blog_entry_delete),
            'DD Mon YYYY HH24:MI:SS') as last_modified
            from users where callsign = %(callsign)s""",
            {'callsign': callsign}))['last_modified']

    return web.json_response(data, headers={'last-modified': last_modified})

@BLOG_ROUTES.post('/aiohttp/blog/{callsign}/comments/read')
@auth()
async def get_blog_comments_read_handler(_data, *, callsign, request, **_):
    blog_callsign = extract_callsign(request)
    comments_read = await DB.execute("""
        select blog_comments_read.entry_id, last_read_comment_id 
        from blog_comments_read join blog_entries on
            blog_entries.id = blog_comments_read.entry_id
        where blog_entries.user = %(blogCallsign)s and
            blog_comments_read.user = %(callsign)s""",
        {"blogCallsign": blog_callsign, "callsign": callsign},
        container="list")
    if not comments_read:
        raise web.HTTPNotFound(text='Blog entries not found')
    return web.json_response({x['entry_id']: x['last_read_comment_id'] for x in comments_read})

@BLOG_ROUTES.put('/aiohttp/blog/{entry_id}/comments/read')
@auth()
async def set_blog_comments_read_handler(data, *, callsign, request, **_):
    entry_id = int(request.match_info.get('entry_id', None))
    if not entry_id:
        raise web.HTTPBadRequest(text='No valid post id was specified.')
    await DB.execute("""
        insert into blog_comments_read (entry_id, "user", last_read_comment_id)
        values (%(entry_id)s, %(callsign)s, %(commentId)s)
        on CONFlict on constraint blog_comments_read_pkey
        do update set last_read_comment_id = %(commentId)s""",
        {"entryId": entry_id, "callsign": callsign, "commentId": data['commentId']})
    return web.Response(text="OK")

@BLOG_ROUTES.delete('/aiohttp/blog/{entry_id}')
@auth(require_email_confirmed=True)
async def delete_blog_entry_handler(_data, *, callsign, request, **_):
    entry_id = int(request.match_info.get('entry_id', None))
    if not entry_id:
        raise web.HTTPBadRequest(text = 'No valid post id was specified.')
    entry_in_db = await DB.execute("""
        select id, "file", file_thumb
        from blog_entries
        where id = %(entryId)s and (%(callsign)s is null or "user" = %(callsign)s)""",
        {'entryId': entry_id, 'callsign': callsign if callsign not in SITE_ADMINS else None})
    if not entry_in_db:
        return web.HTTPNotFound(text='Blog entry not found')
    station_path = await get_station_path_by_admin_cs(callsign)
    await delete_blog_entry(entry_in_db, station_path)
    return web.Response(text='OK')

@BLOG_ROUTES.post('/aiohttp/blog/clear')
@auth(require_email_confirmed=True)
async def clear_blog_handler(_data, *, callsign, **_):
    entries_in_db = await DB.execute("""
        select id, "file", file_thumb
        from blog_entries
        where "user" = %(callsign)s""",
        {'callsign': callsign},
        container="list")
    if entries_in_db:
        station_path = await get_station_path_by_admin_cs(callsign)
        for entry in entries_in_db:
            await delete_blog_entry(entry, station_path)
    return web.Response(text='OK')


@BLOG_ROUTES.delete('/aiohttp/blog/comments/{comment_id}')
@auth(require_email_confirmed=True)
async def delete_blog_comment_handler(_data, *, callsign, request, **_):
    comment_id = int(request.match_info.get('comment_id', None))
    if not comment_id:
        raise web.HTTPBadRequest(text = 'No valid comment id was specified.')
    comment_in_db = await DB.execute("""
        select blog_comments.id 
        from blog_comments join blog_entries 
            on entry_id = blog_entries.id
        where blog_comments.id = %(commentId)s and 
            (%(callsign)s is null or blog_entries.user = %(callsign)s
                or blog_comments.user = %(callsign)s)""",
        {'commentId': comment_id, 'callsign': callsign if callsign not in SITE_ADMINS else None})
    if not comment_in_db:
        return web.HTTPNotFound(text='Blog comment not found')
    await DB.execute("""
        delete from blog_comments 
        where id = %(commentId)s""",
        {'commentId': comment_id})
    return web.Response(text='OK')

@BLOG_ROUTES.post('/aiohttp/blog/{entry_id}/comments')
@auth(require_email_confirmed=True)
async def create_blog_comment_handler(data, *, callsign, request):
    entry_id = int(request.match_info.get('entry_id', None))
    if not entry_id:
        raise web.HTTPBadRequest(text = 'No valid post id was specified.')
    await DB.execute("""
        insert into blog_comments ("user", entry_id, txt)
        values (%(callsign)s, %(entryId)s, %(txt)s)""",
        {"callsign": callsign, "entryId": entry_id, "txt": data["text"]})
    return web.Response(text="OK")

@BLOG_ROUTES.post('/aiohttp/blog/{entry_id}/reactions/{type}')
@auth(require_email_confirmed=True)
async def get_blog_reaction_handler(_data, *, callsign, request, **_):
    entry_id = int(request.match_info.get('entry_id', None))
    reaction_type = int(request.match_info.get('type', None))
    if not entry_id:
        raise web.HTTPBadRequest(text = 'No valid post id was specified.')
    reaction = await DB.execute("""
        select "type"
        from blog_reactions
        where entry_id = %(entryId)s and "user" = %(callsign)s and 
            (%(type)s is null or "type" = %(type)s)""",
        {"entryId": entry_id, "callsign": callsign, "type": reaction_type})
    if not reaction:
        raise web.HTTPNotFound(text='Blog reaction not found')
    return web.json_response(reaction)

@BLOG_ROUTES.put('/aiohttp/blog/{entry_id}/reactions')
@auth(require_email_confirmed=True)
async def create_blog_reaction_handler(data, *, callsign, request, **_):
    entry_id = int(request.match_info.get('entry_id', None))
    if not entry_id:
        raise web.HTTPBadRequest(text = 'No valid post id was specified.')
    await DB.execute("""
        insert into blog_reactions (entry_id, "user", "type")
        values (%(entryId)s, %(callsign)s, %(type)s)
        on CONFlict on constraint blog_reactions_pkey
            do update set "type" = %(type)s""",
        {"callsign": callsign, "entryId": entry_id, "type": data["type"]})
    return web.Response(text="OK")

@BLOG_ROUTES.delete('/aiohttp/blog/{entry_id}/reactions')
@auth(require_email_confirmed=True)
async def delete_blog_reaction_handler(_data, *, callsign, request, **_):
    entry_id = int(request.match_info.get('entry_id', None))
    if not entry_id:
        raise web.HTTPBadRequest(text='No valid post id was specified.')
    await DB.execute("""
        delete from blog_reactions
        where entry_id = %(entryId)s and "user" = %(callsign)s""",
        {'entryId': entry_id, 'callsign': callsign})
    return web.Response(text='OK')

@BLOG_ROUTES.get('/aiohttp/blog/{entry_id}/comments')
async def get_blog_comments_handler(request):
    entry_id = int(request.match_info.get('entry_id', None))
    if not entry_id:
        raise web.HTTPBadRequest(text = 'No valid post id was specified.')
    data = await DB.execute("""
        select id, "user", txt,
            to_char(timestamp_created, 'DD Mon YYYY HH24:MI') as comment_datetime,
            name, chat_callsign, pm_enabled
        from blog_comments join users on blog_comments.user = users.callsign
        where entry_id = %(entryId)s
        order by id""",
        {"entryId": entry_id},
        container="list")
    if not data:
        raise web.HTTPNotFound(text='Blog comments not found')
    return web.json_response(data)

@BLOG_ROUTES.post('/aiohttp/blog')
@BLOG_ROUTES.post('/aiohttp/gallery')
@auth(require_email_confirmed=True)
async def create_blog_entry_handler(data, *, callsign, **_):
    station_path = await get_station_path_by_admin_cs(callsign)
    gallery_path = station_path + '/gallery'
    file = file_type = file_thumb = None
    if 'file' in data:
        post_id = uuid.uuid4().hex
        if data['file']:
            if not os.path.isdir(gallery_path):
                os.mkdir(gallery_path)
            file = data['file']['contents']
            file_name_base = post_id
            file_ext = data['file']['name'].rpartition('.')[2]
            file_name = file_name_base + '.' + file_ext
            file_type = 'image' if 'image'\
                in data['file']['type'] else 'video'
            file_path = gallery_path + '/' + file_name
            with open(file_path, 'wb') as f_img:
                f_img.write(file)
            tn_src = file_path
            if file_type == 'video':

                tn_src = gallery_path + '/' + file_name_base + '.jpeg'
                (
                    ffmpeg
                        .input(file_path)
                        .output(tn_src, vframes=1, vf="thumbnail")
                        .run()
                )
                video_props = ffmpeg.probe(file_path)
                video_stream = [stream for stream in video_props['streams']
                        if stream['codec_type'] == 'video'][0]
                max_video_height = int(CONF['gallery']['max_video_height'])
                if video_stream['height'] > max_video_height:
                    tmp_file_path = f"{gallery_path}/{file_name_base}_tmp.{file_ext}"
                    os.rename(file_path, tmp_file_path)
                    (
                        ffmpeg
                            .output(
                                ffmpeg
                                    .input(tmp_file_path)
                                    .video
                                    .filter('scale', -2, max_video_height),
                                 ffmpeg
                                    .input(tmp_file_path)
                                    .audio,
                                file_path)
                            .run()
                    )
                    os.unlink(tmp_file_path)

            with Image(filename=tn_src) as img:
                with Image(width=img.width, height=img.height,
                        background=Color("#EEEEEE")) as bg_img:

                    bg_img.composite(img, 0, 0)

                    exif = {}
                    exif.update((key[5:], val) for key, val in img.metadata.items() if
                            key.startswith('exif:'))
                    if 'Orientation' in exif:
                        if exif['Orientation'] == '3':
                            bg_img.rotate(180)
                        elif exif['Orientation'] == '6':
                            bg_img.rotate(90)
                        elif exif['Orientation'] == '8':
                            bg_img.rotate(270)

                    size = img.width if img.width < img.height else img.height
                    bg_img.crop(width=size, height=size, gravity='north')
                    bg_img.resize(200, 200)
                    bg_img.format = 'jpeg'
                    bg_img.save(filename=f'{gallery_path}/{file_name_base}_thumb.jpeg')
                    if file_type == 'image':
                        max_height, max_width = (int(CONF['gallery']['max_height']),
                                int(CONF['gallery']['max_width']))
                        if img.width > max_width or img.height > max_height:
                            coeff = min(max_width/img.width, max_height/img.height)
                            img.resize(width=int(coeff*img.width), height=int(coeff*img.height))
                            img.compression_quality = int(CONF['gallery']['quality'])
                            img.save(filename=file_path)
            if file_type == 'video':
                os.unlink(tn_src)

            file = f'gallery/{file_name}'
            file_thumb = f'gallery/{file_name_base}_thumb.jpeg'

        await DB.execute("""
            insert into blog_entries
                ("user", "file", file_type, file_thumb, txt)
            values
                (%(callsign)s, %(file)s, %(fileType)s, %(fileThumb)s, %(text)s)
            """,
            params={'callsign': callsign, 'file': file, 'fileType': file_type,
                'fileThumb': file_thumb, 'text': data['caption']})

        return web.Response(text='OK')

async def get_user_gallery_quota(callsign):
    user_coeff = (await DB.execute("""
        select gallery_quotas
        from users
        where callsign = %(callsign)s""", {'callsign': callsign}))['gallery_quotas']
    return int(CONF['gallery']['quota'])*user_coeff

@BLOG_ROUTES.post('/aiohttp/blog/quota')
@auth(require_email_confirmed=True)
async def get_blog_quota_handler(_data, *, callsign, **_):
    return web.json_response({
        'quota': await get_user_gallery_quota(callsign),
        'used': get_gallery_size(await get_station_path_by_admin_cs(callsign))
        })
