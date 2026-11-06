"""harkcam mode: webrtc. The SDP translation between Mainsail/Fluidd's WHEP
client and Creality's webrtc_local, and the WHEP endpoints against a fake
webrtc_local that speaks its base64 JSON signalling."""
import base64
import hashlib
import http.server
import os
import socket
import struct
import importlib.util
import json
import pathlib
import threading
import unittest
import urllib.error
import urllib.request

PATH = pathlib.Path(__file__).parents[1] / "harkcam" / "harkcam.py"
spec = importlib.util.spec_from_file_location("harkcam_webrtc", PATH)
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

# What Chrome makes for Mainsail's WebrtcMediaMTX: video + audio sendrecv,
# bundled, no candidates yet (sent before gathering)
OFFER = "\r\n".join([
    "v=0", "o=- 4611731400430051336 2 IN IP4 127.0.0.1", "s=-", "t=0 0",
    "a=group:BUNDLE 0 1", "a=extmap-allow-mixed", "a=msid-semantic: WMS",
    "m=video 9 UDP/TLS/RTP/SAVPF 96 97 102 103", "c=IN IP4 0.0.0.0",
    "a=rtcp:9 IN IP4 0.0.0.0", "a=ice-ufrag:abcd", "a=ice-pwd:0123456789",
    "a=ice-options:trickle", "a=fingerprint:sha-256 AA:BB", "a=setup:actpass",
    "a=mid:0", "a=sendrecv", "a=rtcp-mux", "a=rtpmap:96 VP8/90000",
    "a=rtpmap:102 H264/90000",
    "a=fmtp:102 level-asymmetry-allowed=1;packetization-mode=1;"
    "profile-level-id=42001f",
    "m=audio 9 UDP/TLS/RTP/SAVPF 111 63", "c=IN IP4 0.0.0.0",
    "a=rtcp:9 IN IP4 0.0.0.0", "a=ice-ufrag:abcd", "a=ice-pwd:0123456789",
    "a=fingerprint:sha-256 AA:BB", "a=setup:actpass", "a=mid:1",
    "a=sendrecv", "a=rtcp-mux", "a=rtpmap:111 opus/48000/2", ""])

# The same after gathering, as the test page sends it
GATHERED = OFFER.replace("a=mid:0\r\n", "a=mid:0\r\n"
                         "a=candidate:1 1 udp 1 10.0.0.2 5000 typ host\r\n")

# A plausible webrtc_local answer: one video section, its own mid, its own
# bundle line, a host candidate
CREALITY_ANSWER = "\r\n".join([
    "v=0", "o=- 1 1 IN IP4 0.0.0.0", "s=-", "t=0 0", "a=group:BUNDLE video",
    "m=video 9 UDP/TLS/RTP/SAVPF 102", "c=IN IP4 0.0.0.0",
    "a=ice-ufrag:wxyz", "a=ice-pwd:9876543210", "a=fingerprint:sha-256 CC:DD",
    "a=setup:passive", "a=mid:video", "a=sendonly", "a=rtcp-mux",
    "a=rtpmap:102 H264/90000",
    "a=candidate:1 1 UDP 2015363327 192.168.1.50 50000 typ host", ""])


def sections(sdp):
    return hc.split_sdp(sdp)[1]


class SdpTest(unittest.TestCase):
    def test_offer_keeps_video_only(self):
        sdp, plan = hc.offer_for_creality(OFFER)
        media = sections(sdp)
        self.assertEqual(len(media), 1)
        self.assertTrue(media[0][0].startswith("m=video"))
        self.assertIn("a=group:BUNDLE 0", sdp.split("\r\n"))
        self.assertNotIn("opus", sdp)
        self.assertEqual(plan, [("keep", "0"),
                                ("refuse", "audio 0 UDP/TLS/RTP/SAVPF 111",
                                 "1", ["a=rtpmap:111 opus/48000/2"])])
        self.assertTrue(sdp.endswith("\r\n") and "\n\n" not in sdp)

    def test_answer_matches_the_offer(self):
        _, plan = hc.offer_for_creality(OFFER)
        ans = hc.answer_for_browser(CREALITY_ANSWER, plan)
        media = sections(ans)
        self.assertEqual([m[0].split()[0] for m in media],
                         ["m=video", "m=audio"])
        self.assertEqual(hc.media_mid(media[0]), "0")
        self.assertEqual(sum(l.startswith("a=mid:") for l in media[0]), 1)
        self.assertEqual(media[1][0], "m=audio 0 UDP/TLS/RTP/SAVPF 111")
        self.assertIn("a=inactive", media[1])
        self.assertEqual(hc.media_mid(media[1]), "1")
        # carries the bundle's ICE/DTLS lines (aiortc refused it without)
        for line in ("a=ice-ufrag:wxyz", "a=ice-pwd:9876543210",
                     "a=fingerprint:sha-256 CC:DD", "a=setup:passive",
                     "a=rtcp-mux", "a=rtpmap:111 opus/48000/2"):
            self.assertIn(line, media[1])
        lines = ans.split("\r\n")
        self.assertEqual([l for l in lines if l.startswith("a=group")],
                         ["a=group:BUNDLE 0"])
        # the group line sits after t= in the session part
        self.assertLess(lines.index("t=0 0"), lines.index("a=group:BUNDLE 0"))
        self.assertLess(lines.index("a=group:BUNDLE 0"),
                        lines.index("m=video 9 UDP/TLS/RTP/SAVPF 102"))
        # the rest of webrtc_local's video section is untouched
        for keep in ("a=candidate:1 1 UDP 2015363327 192.168.1.50 50000 typ "
                     "host", "a=rtpmap:102 H264/90000", "a=setup:passive",
                     "a=sendonly"):
            self.assertIn(keep, media[0])

    def test_video_only_offer_and_answer_without_mid(self):
        offer = OFFER.split("m=audio")[0].replace("a=group:BUNDLE 0 1",
                                                  "a=group:BUNDLE 0")
        sdp, plan = hc.offer_for_creality(offer)
        self.assertEqual(plan, [("keep", "0")])
        ans = hc.answer_for_browser(
            CREALITY_ANSWER.replace("a=mid:video\r\n", "").replace(
                "a=group:BUNDLE video\r\n", ""), plan)
        media = sections(ans)
        self.assertEqual(len(media), 1)
        self.assertEqual(hc.media_mid(media[0]), "0")
        self.assertIn("a=group:BUNDLE 0", ans.split("\r\n"))

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            hc.offer_for_creality(OFFER.split("m=video")[0])
        with self.assertRaises(ValueError):
            hc.answer_for_browser("v=0\r\ns=-\r\n", [("keep", "0")])

    def test_fix_candidate(self):
        mdns = ("candidate:842163049 1 udp 1677729535 "
                "0a1b2c3d-1111-2222-3333-444455556666.local 54321 typ host "
                "generation 0")
        self.assertEqual(hc.fix_candidate(mdns, "192.168.3.98"),
                         "a=candidate:842163049 1 udp 1677729535 "
                         "192.168.3.98 54321 typ host generation 0")
        self.assertIsNone(hc.fix_candidate(mdns, None))
        self.assertIsNone(hc.fix_candidate(mdns, "fe80::1"))
        self.assertEqual(hc.fix_candidate(
            "a=candidate:1 1 UDP 2122260223 192.168.3.98 50000 typ host",
            None), "a=candidate:1 1 UDP 2122260223 192.168.3.98 50000 "
                   "typ host")
        self.assertIsNone(hc.fix_candidate(
            "candidate:2 1 tcp 1518280447 192.168.3.98 9 typ host "
            "tcptype active", None))
        self.assertIsNone(hc.fix_candidate(
            "candidate:3 1 udp 2122262783 fd00::5 50001 typ host", None))
        self.assertIsNone(hc.fix_candidate("garbage", None))

    def test_offer_gets_the_candidates(self):
        sdp, _ = hc.offer_for_creality(OFFER, [
            "candidate:9 1 udp 1677729535 abcd.local 51000 typ host",
            "candidate:9 1 udp 1677729535 abcd.local 51000 typ host",
            "candidate:8 1 udp 1677729535 81.2.3.4 51001 typ srflx raddr "
            "0.0.0.0 rport 0"], "192.168.3.98")
        video = sections(sdp)[0]
        cands = [l for l in video if l.startswith("a=candidate")]
        self.assertEqual(cands, [
            "a=candidate:9 1 udp 1677729535 192.168.3.98 51000 typ host",
            "a=candidate:8 1 udp 1677729535 81.2.3.4 51001 typ srflx raddr "
            "0.0.0.0 rport 0"])
        self.assertEqual(video[-1], "a=end-of-candidates")

    def test_mode_settings(self):
        self.assertEqual(hc.mode_settings({}), (
            "mjpeg", "http://127.0.0.1:8000/call/webrtc_local"))
        self.assertEqual(hc.mode_settings({"mode": " WebRTC "})[0], "webrtc")
        self.assertEqual(hc.mode_settings({"mode": "h264"})[0], "mjpeg")


class FakeWebrtcLocal(http.server.BaseHTTPRequestHandler):
    offers = []
    reply = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        msg = json.loads(base64.b64decode(body))
        FakeWebrtcLocal.offers.append(msg)
        reply = FakeWebrtcLocal.reply or {"type": "answer",
                                          "sdp": CREALITY_ANSWER}
        out = base64.b64encode(json.dumps(reply).encode())
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class WhepTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                                   FakeWebrtcLocal)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.srv = hc.Server(("127.0.0.1", 0), hc.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.fake.shutdown()

    def setUp(self):
        hc.MODE = "webrtc"
        hc.WEBRTC_URL = "http://127.0.0.1:%d/call/webrtc_local" % \
            self.fake.server_address[1]
        FakeWebrtcLocal.offers, FakeWebrtcLocal.reply = [], None

    def req(self, method, path, body=None, ctype="application/sdp"):
        r = urllib.request.Request(self.base + path, method=method,
                                   data=body.encode() if body else None,
                                   headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.headers, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode()

    def test_whep_session(self):
        code, h, _ = self.req("OPTIONS", "/whep")
        self.assertEqual(code, 204)
        self.assertIn("Location", h["Access-Control-Expose-Headers"])
        self.assertIsNone(h.get("Link"))           # no ICE servers
        code, h, body = self.req("POST", "/whep", GATHERED)
        self.assertEqual(code, 201)
        self.assertEqual(h["Content-Type"], "application/sdp")
        self.assertTrue(h["Location"].startswith("whep/"))
        self.assertTrue(h["ETag"])
        self.assertEqual(len(sections(body)), 2)
        # webrtc_local saw the video-only offer, in its own wrapping
        sent = FakeWebrtcLocal.offers[0]
        self.assertEqual(sent["type"], "offer")
        self.assertEqual(len(sections(sent["sdp"])), 1)
        sid = h["Location"].split("/")[-1]
        self.assertEqual(self.req("PATCH", "/whep/" + sid, "a=candidate:x",
                                  "application/trickle-ice-sdpfrag")[0], 204)
        self.assertEqual(self.req("DELETE", "/whep/" + sid)[0], 200)

    def test_errors(self):
        FakeWebrtcLocal.reply = {"type": "error"}
        self.assertEqual(self.req("POST", "/whep", GATHERED)[0], 502)
        hc.WEBRTC_URL = "http://127.0.0.1:9/call/webrtc_local"
        code, _, body = self.req("POST", "/whep", GATHERED)
        self.assertEqual(code, 502)
        self.assertIn("webrtc_local", body)
        self.assertEqual(self.req("PATCH", "/whep")[0], 404)
        self.assertEqual(self.req("POST", "/other", OFFER)[0], 404)

    def test_mjpeg_routes_in_webrtc_mode(self):
        code, _, body = self.req("GET", "/?action=snapshot")
        self.assertEqual(code, 503)
        self.assertIn("mode: mjpeg", body)
        code, h, body = self.req("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("RTCPeerConnection", body)

    def ws(self, messages, wait_answer=True, keep=False):
        """A go2rtc-style WebSocket client: send messages, return replies."""
        sock = socket.create_connection(("127.0.0.1",
                                         self.srv.server_address[1]))
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(("GET /api/ws?src=k2&media=video+audio HTTP/1.1\r\n"
                      "Host: x\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n" % key).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += sock.recv(1)
        self.assertIn(b"101", head.split(b"\r\n")[0])
        accept = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode())
            .digest())
        self.assertIn(accept, head)
        for m in messages:
            data = json.dumps(m).encode()
            mask = os.urandom(4)
            n = len(data)
            hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else
                                   struct.pack(">BH", 0x80 | 126, n))
            sock.sendall(hdr + mask + bytes(
                b ^ mask[i % 4] for i, b in enumerate(data)))
        replies = []
        if wait_answer:
            sock.settimeout(8)
            buf = b""
            while True:
                buf += sock.recv(65536)
                if len(buf) >= 4:
                    n = buf[1] & 0x7F
                    off = 2
                    if n == 126:
                        n = struct.unpack(">H", buf[2:4])[0]
                        off = 4
                    if len(buf) >= off + n:
                        replies.append(json.loads(buf[off:off + n]))
                        break
        if keep:
            return replies, sock
        sock.sendall(bytes([0x88, 0x80]) + os.urandom(4))   # close
        sock.close()
        return replies

    def test_go2rtc_websocket(self):
        reply = self.ws([
            {"type": "webrtc/offer", "value": OFFER},
            {"type": "webrtc/candidate", "value":
             "candidate:9 1 udp 1677729535 abcd.local 51000 typ host"}])
        self.assertEqual(reply[0]["type"], "webrtc/answer")
        self.assertEqual(len(sections(reply[0]["value"])), 2)
        sent = FakeWebrtcLocal.offers[0]["sdp"]
        # the candidate went into the offer, with the peer's address
        self.assertIn("a=candidate:9 1 udp 1677729535 127.0.0.1 51000 typ "
                      "host", sent.split("\r\n"))

    def test_go2rtc_websocket_error(self):
        FakeWebrtcLocal.reply = {"type": "error"}
        reply = self.ws([{"type": "webrtc/offer", "value": OFFER},
                         {"type": "webrtc/candidate", "value":
                          "candidate:1 1 udp 1 10.0.0.2 5000 typ host"}])
        self.assertEqual(reply[0]["type"], "error")

    def test_an_offer_without_candidates_never_reaches_webrtc_local(self):
        # Mainsail's MediaMTX type: it would leave a session with no video
        FakeWebrtcLocal.offers.clear()
        code, _, body = self.req("POST", "/whep", OFFER)
        self.assertEqual(code, 400)
        self.assertIn("go2rtc", body)
        self.assertEqual(FakeWebrtcLocal.offers, [])

    def test_viewers_past_the_cap_are_refused(self):
        msgs = [{"type": "webrtc/offer", "value": OFFER},
                {"type": "webrtc/candidate", "value":
                 "candidate:1 1 udp 1 10.0.0.2 5000 typ host"}]
        saved = hc.VIEWERS, hc.VIEWER_WAIT
        hc.VIEWERS, hc.VIEWER_WAIT = hc.Viewers(1), 0.2
        try:
            first, sock = self.ws(msgs, keep=True)
            self.assertEqual(first[0]["type"], "webrtc/answer")
            FakeWebrtcLocal.offers.clear()
            second = self.ws(msgs)
            self.assertEqual(second[0]["type"], "error")
            self.assertIn("1 viewers", second[0]["value"])
            self.assertEqual(FakeWebrtcLocal.offers, [])
            # leaving gives the place back
            sock.sendall(bytes([0x88, 0x80]) + os.urandom(4))
            sock.close()
            hc.VIEWER_WAIT = 3.0
            third = self.ws(msgs)
            self.assertEqual(third[0]["type"], "webrtc/answer")
        finally:
            hc.VIEWERS, hc.VIEWER_WAIT = saved

    def test_a_failed_call_gives_the_place_back(self):
        saved = hc.VIEWERS
        hc.VIEWERS = hc.Viewers(1)
        FakeWebrtcLocal.reply = {"type": "error"}
        try:
            self.ws([{"type": "webrtc/offer", "value": OFFER},
                     {"type": "webrtc/candidate", "value":
                      "candidate:1 1 udp 1 10.0.0.2 5000 typ host"}])
            self.assertTrue(hc.VIEWERS.enter(1.0))
        finally:
            hc.VIEWERS = saved

    def test_whep_refused_in_mjpeg_mode(self):
        hc.MODE = "mjpeg"
        self.assertEqual(self.req("POST", "/whep", OFFER)[0], 409)


if __name__ == "__main__":
    unittest.main()
