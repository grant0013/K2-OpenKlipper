"""harkcam mode: webrtc. The SDP translation between Mainsail/Fluidd's WHEP
client and Creality's webrtc_local, and the WHEP endpoints against a fake
webrtc_local that speaks its base64 JSON signalling."""
import base64
import http.server
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
        code, h, body = self.req("POST", "/whep", OFFER)
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
        self.assertEqual(self.req("POST", "/whep", OFFER)[0], 502)
        hc.WEBRTC_URL = "http://127.0.0.1:9/call/webrtc_local"
        code, _, body = self.req("POST", "/whep", OFFER)
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

    def test_whep_refused_in_mjpeg_mode(self):
        hc.MODE = "mjpeg"
        self.assertEqual(self.req("POST", "/whep", OFFER)[0], 409)


if __name__ == "__main__":
    unittest.main()
