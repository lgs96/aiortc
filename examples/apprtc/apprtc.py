import json
import argparse
import asyncio
import logging
import os
import random

import cv2
from av import VideoFrame

from aiortc import (
    RTCIceCandidate,
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
)
from aiortc.contrib.media import MediaBlackhole, MediaPlayer, MediaRecorder
from aiortc.contrib.signaling import BYE
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp

ROOT = os.path.dirname(__file__)
PHOTO_PATH = os.path.join(ROOT, "photo.jpg")

logger = logging.getLogger(__name__)
try:
    import aiohttp
    import websockets
except ImportError:  # pragma: no cover
    aiohttp = None
    websockets = None


def object_from_string(message_str):
    message = json.loads(message_str)
    if message["type"] in ["answer", "offer"]:
        return RTCSessionDescription(**message)
    elif message["type"] == "candidate" and message["candidate"]:
        candidate = candidate_from_sdp(message["candidate"].split(":", 1)[1])
        candidate.sdpMid = message["id"]
        candidate.sdpMLineIndex = message["label"]
        return candidate
    elif message["type"] == "bye":
        return BYE


def object_to_string(obj):
    if isinstance(obj, RTCSessionDescription):
        message = {"sdp": obj.sdp, "type": obj.type}
    elif isinstance(obj, RTCIceCandidate):
        message = {
            "candidate": "candidate:" + candidate_to_sdp(obj),
            "id": obj.sdpMid,
            "label": obj.sdpMLineIndex,
            "type": "candidate",
        }
    else:
        assert obj is BYE
        message = {"type": "bye"}
    return json.dumps(message, sort_keys=True)

class ApprtcSignaling:
    def __init__(self, room):
        self._http = None
        self._origin = "https://goodsol.overlinkapp.org"
        self._room = room
        self._websocket = None

    async def connect(self):
        join_url = self._origin + "/join/" + self._room

        # fetch room parameters
        self._http = aiohttp.ClientSession()
        async with self._http.post(join_url) as response:
            # we cannot use response.json() due to:
            # https://github.com/webrtc/apprtc/issues/562
            data = json.loads(await response.text())
        assert data["result"] == "SUCCESS"
        params = data["params"]

        self.__is_initiator = params["is_initiator"] == "true"
        self.__messages = params["messages"]
        self.__post_url = (
            self._origin + "/message/" + self._room + "/" + params["client_id"]
        )

        # connect to websocket
        self._websocket = await websockets.connect(
            params["wss_url"], extra_headers={"Origin": self._origin}
        )
        await self._websocket.send(
            json.dumps(
                {
                    "clientid": params["client_id"],
                    "cmd": "register",
                    "roomid": params["room_id"],
                }
            )
        )

        print(f"AppRTC room is {params['room_id']} {params['room_link']}")

        return params

    async def close(self):
        if self._websocket:
            await self.send(BYE)
            await self._websocket.close()
        if self._http:
            await self._http.close()

    async def receive(self):
        if self.__messages:
            message = self.__messages.pop(0)
        else:
            message = await self._websocket.recv()
            message = json.loads(message)["msg"]
        logger.info("< " + message)
        return object_from_string(message)

    async def send(self, obj):
        message = object_to_string(obj)
        logger.info("> " + message)
        if self.__is_initiator:
            await self._http.post(self.__post_url, data=message)
        else:
            await self._websocket.send(json.dumps({"cmd": "send", "msg": message}))

class VideoImageTrack(VideoStreamTrack):
    """
    A video stream track that returns a rotating image.
    """

    def __init__(self):
        super().__init__()  # don't forget this!
        self.img = cv2.imread(PHOTO_PATH, cv2.IMREAD_COLOR)

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        # rotate image
        rows, cols, _ = self.img.shape
        M = cv2.getRotationMatrix2D((cols / 2, rows / 2), int(pts * time_base * 45), 1)
        img = cv2.warpAffine(self.img, M, (cols, rows))

        # create video frame
        frame = VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base

        return frame


async def run(pc, player, recorder, signaling):
    def add_tracks():
        if player and player.audio:
            pc.addTrack(player.audio)

        if player and player.video:
            pc.addTrack(player.video)
        else:
            pc.addTrack(VideoImageTrack())

    @pc.on("track")
    def on_track(track):
        print("Track %s received" % track.kind)
        recorder.addTrack(track)

    # connect to websocket and join
    params = await signaling.connect()

    if params["is_initiator"] == "true":
        # send offer
        add_tracks()
        await pc.setLocalDescription(await pc.createOffer())
        await signaling.send(pc.localDescription)

    # consume signaling
    while True:
        obj = await signaling.receive()

        if isinstance(obj, RTCSessionDescription):
            await pc.setRemoteDescription(obj)
            await recorder.start()

            if obj.type == "offer":
                # send answer
                add_tracks()
                await pc.setLocalDescription(await pc.createAnswer())
                await signaling.send(pc.localDescription)
        elif isinstance(obj, RTCIceCandidate):
            await pc.addIceCandidate(obj)
        elif obj is BYE:
            print("Exiting")
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AppRTC")
    parser.add_argument("room", nargs="?")
    parser.add_argument("--play-from", help="Read the media from a file and sent it."),
    parser.add_argument("--record-to", help="Write received media to a file."),
    parser.add_argument("--verbose", "-v", action="count")
    args = parser.parse_args()

    if not args.room:
        args.room = "".join([random.choice("0123456789") for x in range(10)])

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    # create signaling and peer connection
    signaling = ApprtcSignaling(args.room)
    pc = RTCPeerConnection()

    # create media source
    if args.play_from:
        player = MediaPlayer(args.play_from)
    else:
        player = None

    # create media sink
    if args.record_to:
        recorder = MediaRecorder(args.record_to)
    else:
        recorder = MediaBlackhole()

    # run event loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(
            run(pc=pc, player=player, recorder=recorder, signaling=signaling)
        )
    except KeyboardInterrupt:
        pass
    finally:
        # cleanup
        loop.run_until_complete(recorder.stop())
        loop.run_until_complete(signaling.close())
        loop.run_until_complete(pc.close())
