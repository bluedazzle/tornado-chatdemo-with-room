#!/usr/bin/env python
#
# Copyright 2009 Facebook
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import logging

import time
import tornado.escape
import tornado.ioloop
import tornado.web
import os.path
import uuid

from tornado.concurrent import Future
from tornado import gen
from tornado.options import define, options, parse_command_line

define("port", default=8888, help="run on the given port", type=int)
define("debug", default=False, help="run in debug mode")


class MessageBuffer(object):
    def __init__(self):
        self.room = {}
        self.cache_size = 200

    def wait_for_messages(self, room, cursor=None):
        # Construct a Future to return to our caller.  This allows
        # wait_for_messages to be yielded from a coroutine even though
        # it is not a coroutine itself.  We will set the result of the
        # Future when results are available.
        self.create_new_room(room)
        result_future = Future()
        if cursor:
            new_count = 0
            for msg in reversed(self.room[room]['cache']):
                if msg["id"] == cursor:
                    break
                new_count += 1
            if new_count:
                result_future.set_result(self.room[room]['cache'][-new_count:])
                return result_future
        self.room[room]['waiters'].add(result_future)
        return result_future

    def cancel_wait(self, room, future):
        self.room[room]['waiters'].remove(future)
        # Set an empty result to unblock any coroutines waiting.
        future.set_result([])

    def new_messages(self, room, messages):
        self.create_new_room(room)
        logging.info("Sending new message to %r listeners", len(self.room[room]['waiters']))
        for future in self.room[room]['waiters']:
            future.set_result(messages)
        self.room[room]['waiters'] = set()
        self.room[room]['cache'].extend(messages)
        if len(self.room[room]['cache']) > self.cache_size:
            self.room[room]['cache'] = self.room[room]['cache'][-self.cache_size:]

    def create_new_room(self, room):
        if room not in self.room:
            self.room[room] = {'cache': [],
                               'waiters': set()}
        return True


# Making this a non-singleton is left as an exercise for the reader.
global_message_buffer = MessageBuffer()


class MainHandler(tornado.web.RequestHandler):
    def get(self):
        room = self.get_argument("room", None)
        if room:
            global_message_buffer.create_new_room(room)
            self.render("index.html", messages=global_message_buffer.room[room]['cache'], room=room)


class MessageNewHandler(tornado.web.RequestHandler):
    def post(self):
        message = {
            "id": str(uuid.uuid4()),
            "timestamp": str(time.time()).split('.')[0],
            "body": self.get_argument("body"),
        }
        # to_basestring is necessary for Python 3's json encoder,
        # which doesn't accept byte strings.
        # message["html"] = message
        if self.get_argument("next", None):
            self.redirect(self.get_argument("next"))
        else:
            self.write(message)
        room = self.get_argument("room", None)
        global_message_buffer.new_messages(room, [message])


class MessageUpdatesHandler(tornado.web.RequestHandler):
    @gen.coroutine
    def post(self):
        cursor = self.get_argument("cursor", None)
        self.room = self.get_argument("room", None)
        # Save the future returned by wait_for_messages so we can cancel
        # it in wait_for_messages
        self.future = global_message_buffer.wait_for_messages(self.room, cursor=cursor)
        messages = yield self.future
        if self.request.connection.stream.closed():
            return
        self.write(dict(messages=messages))

    def on_connection_close(self):
        global_message_buffer.cancel_wait(self.room, self.future)


class RoomHandler(tornado.web.RequestHandler):
    def get(self, *args, **kwargs):
        self.render("room.html")


def main():
    parse_command_line()
    app = tornado.web.Application(
        [
            (r"/", RoomHandler),
            (r"/room", MainHandler),
            (r"/a/message/new", MessageNewHandler),
            (r"/a/message/updates", MessageUpdatesHandler),
            ],
        cookie_secret="__TODO:_GENERATE_YOUR_OWN_RANDOM_VALUE_HERE__",
        template_path=os.path.join(os.path.dirname(__file__), "templates"),
        static_path=os.path.join(os.path.dirname(__file__), "static"),
        xsrf_cookies=False,
        debug=options.debug,
        )
    app.listen(options.port)
    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
