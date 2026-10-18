import math
import os
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import wave
from pathlib import Path
from urllib.parse import quote
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np
from engine import AudioCache, Project, correlate_features, sync


def wav(path, arr):
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1);wf.setsampwidth(2);wf.setframerate(8000)
        wf.writeframes((np.clip(arr,-1,1)*32767).astype('<i2').tobytes())


def clip(uid, name, path, start, duration, link_to=()):
    links = ''.join('<link><linkclipref>%s</linkclipref></link>'%l for l in link_to)
    return (f'<clipitem id="{uid}"><name>{name}</name><duration>{duration}</duration>'
            f'<start>{start}</start><end>{start+duration}</end><in>0</in><out>{duration}</out>'
            f'<file id="file-{uid}"><pathurl>{Path(path).as_uri()}</pathurl><rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate></file>{links}</clipitem>')


class TestSync(unittest.TestCase):
    def test_synthetic_two_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1=Path(tmp)/'rec.wav';p2=Path(tmp)/'cam.wav'
            rng=np.random.default_rng(143)
            sr=8000
            t=np.arange(sr*72)/sr
            # audio modulado com vozes / picos, mais parecido com material falado
            noise=rng.normal(size=len(t))
            tone=np.sin(2*np.pi*440*t)
            amp=np.maximum(0,np.sin(2*np.pi*2.1*t)+.4*np.sin(2*np.pi*.43*t))
            audio=(noise*.17+tone*.13)*amp
            wav(p1,audio)
            start=19
            wav(p2,audio[sr*start:sr*(start+24)]*.8+rng.normal(size=sr*24)*.008)
            xml=(f'<?xml version="1.0"?><!DOCTYPE xmeml><xmeml version="5"><sequence><name>T</name><duration>1800</duration><rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate><media>'
                 f'<video><track>{clip("cam-video", "CAM 01", p2,0,24*25, ("cam-audio",))}</track></video>'
                 f'<audio><track>{clip("rec-audio", "REC", p1,0,72*25)}</track><track>{clip("cam-audio", "CAM audio", p2,0,24*25,("cam-video",))}</track></audio>'
                 '</media></sequence></xmeml>').encode()
            project=Project(xml)
            self.assertEqual(len(project.groups),2)
            res=sync(project,'audio-1', threshold=.24)
            print('RESULT',res['synced'],res['groups'],res['collisions'])
            self.assertEqual(res['synced'],1)
            group=next(g for g in project.groups if g.title=='CAM 01')
            self.assertAlmostEqual(group.assigned,start,delta=.12)
            self.assertEqual(res['collisions'],[])
            output=project.to_xml()
            restored=Project(output)
            self.assertAlmostEqual(next(c.start for c in restored.clips if c.name=='CAM 01'),start,delta=1/25)
            self.assertTrue(restored.has_doctype)

    def test_feature_lags_sign(self):
        rng=np.random.default_rng(34)
        a=rng.normal(size=(500,5)).astype(np.float32)
        b=a[128:300]+rng.normal(size=(172,5)).astype(np.float32)*.05
        result=correlate_features(a,b)
        self.assertAlmostEqual(result[0],128/50,places=4)
        self.assertGreater(result[1],.9)

    def test_disallow_wrong_xml(self):
        with self.assertRaisesRegex(ValueError, 'Final Cut Pro 7'):
            Project(b'<fcpxml version="1.0"/>')

if __name__=='__main__': unittest.main()

class TestEdges(unittest.TestCase):
    def test_relink_when_unique(self):
        from engine import locate_media
        with tempfile.TemporaryDirectory() as t:
            newdir=Path(t)/'Novos Brutos';newdir.mkdir()
            good=newdir/'C0001.wav';wav(good,np.zeros(8000*3))
            old='/Volumes/SSD Antigo/C0001.wav'
            cxml=(f'<xmeml version="5"><sequence><rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate><media><audio><track>'
                  f'<clipitem id="cam"><name>Cam</name><start>0</start><end>75</end><in>0</in><out>75</out><file id="file1"><pathurl>file://localhost/Volumes/SSD%20Antigo/C0001.wav</pathurl></file></clipitem>'
                  '</track></audio></media></sequence></xmeml>').encode()
            p=Project(cxml)
            locate_media(p,str(newdir))
            self.assertEqual(p.groups[0].path,str(good))
            self.assertIn(good.as_uri().encode(),p.to_xml())

    def test_ntsc_fps(self):
        p=Project(b'<xmeml version="5"><sequence><rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate><media><video><track><clipitem id="x"><start>0</start><end>300</end><in>0</in><out>300</out><file><pathurl>file:///tmp/demo.mov</pathurl></file></clipitem></track></video></media></sequence></xmeml>')
        self.assertAlmostEqual(p.fps,29.97002997)
        self.assertAlmostEqual(p.clips[0].duration,10.01,places=2)

class TestMulticam(unittest.TestCase):
    def test_three_cameras_bridge_across_different_tracks(self):
        # Camera A has no overlapping sound with camera C: B must bridge the gap.
        with tempfile.TemporaryDirectory() as tmp:
            rng = np.random.default_rng(889)
            sr = 8000
            src = rng.normal(size=sr*94).astype(np.float32)
            # Distinctive background and transients across a long recording.
            src *= (0.25 + 0.5 * (1+np.sin(np.arange(len(src))/sr*1.8))/2)
            clips = [('A',0,45), ('B',31,40), ('C',60,24)]
            paths = {}
            for name, st, seconds in clips:
                path = Path(tmp)/(name+'.wav')
                audio = src[sr*st:sr*(st+seconds)]
                wav(path, audio*.8 + rng.normal(size=len(audio))*.003)
                paths[name] = path
            def node(name, st, duration, track, kind):
                return clip(name+'-'+kind, name+' '+kind,
                            paths[name], 0, duration*25,
                            (name+('-audio' if kind == 'video' else '-video'),))
            vtracks = ''.join('<track>'+node(n,st,d,'', 'video')+'</track>' for n,st,d in clips)
            atracks = ''.join('<track>'+node(n,st,d,'', 'audio')+'</track>' for n,st,d in clips)
            xml=(f'<xmeml version="5"><sequence><name>Múltiplas câmeras</name><rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>'
                 f'<media><video>{vtracks}</video><audio>{atracks}</audio></media></sequence></xmeml>').encode()
            pr = Project(xml)
            self.assertEqual(pr.summary()['source_groups'],3)
            self.assertEqual(len(pr.tracks()), 6)
            result=sync(pr, 'auto', threshold=.60)
            self.assertEqual(result['synced'], 2, result)
            self.assertEqual(result['reference'], 'video-1')
            self.assertAlmostEqual(next(g.assigned for g in pr.groups if g.title=='B video'),31, delta=.1)
            self.assertAlmostEqual(next(g.assigned for g in pr.groups if g.title=='C video'),60, delta=.1)
            output=pr.to_xml()
            restored=Project(output)
            self.assertEqual(len(restored.clips),6)
            self.assertAlmostEqual(next(c.start for c in restored.clips if c.name=='C video'),60,delta=1/25)

    def test_overlap_creates_extra_lanes_and_updates_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'demo.wav'
            wav(f, np.zeros(8000*4))
            # Two recordings start sequentially on the same V/A tracks.
            xml=(f'<xmeml version="5"><sequence><rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate><media>'
                 f'<video><track>{clip("v1","V1", f, 0, 250, ["a1"])}'
                 f'{clip("v2","V2", f, 250, 250, ["a2"])}</track></video>'
                 f'<audio><track>{clip("a1","A1", f, 0, 250, ["v1"])}'
                 f'{clip("a2","A2", f, 250, 250, ["v2"])}</track></audio>'
                 '</media></sequence></xmeml>').encode()
            pr=Project(xml)
            # Include real FCP7 link metadata, which must be corrected after
            # splitting original video/audio tracks into more lanes.
            for c in pr.clips:
                for node in c.element.findall('./link'):
                    ET.SubElement(node, 'mediatype').text = ('audio' if c.kind == 'video' else 'video')
                    ET.SubElement(node, 'trackindex').text = '1'
                    ET.SubElement(node, 'clipindex').text = '2' if c.uid.endswith('2') else '1'
                c.new_start = 0 if c.uid.endswith('1') else 5
            exported=pr.to_xml()
            self.assertEqual(exported, pr.to_xml(), 'repeated downloads must be idempotent')
            root = ET.fromstring(exported)
            self.assertEqual(len(root.findall('.//media/video/track')), 2)
            self.assertEqual(len(root.findall('.//media/audio/track')), 2)
            self.assertEqual(pr.lanes_added, 2)
            self.assertEqual(len(list(root.iter('clipitem'))),4)
            a2=root.find('.//clipitem[@id="a2"]')
            link=a2.find('./link')
            self.assertEqual(link.findtext('linkclipref'), 'v2')
            self.assertEqual(link.findtext('trackindex'), '2')
            self.assertEqual(link.findtext('clipindex'), '1')
            self.assertEqual(root.find('.//clipitem[@id="v2"]/link/trackindex').text, '2')

    def test_track_order_does_not_overrule_distinct_recordings(self):
        # All correlations are simulated. Two different recorded files may be
        # placed sequentially on one editing track even if their real recording
        # times are in another order. Track order is not a source clock.
        source = Path('/tmp/example')
        parts = [('ref','base',0,100), ('first','cam',0,10), ('second','cam',10,10)]
        tracks = []
        for name, kind, start, dur in parts:
            xmlclip=clip(name, name, source / (name+'.wav'), start*25, dur*25)
            tracks.append('<track>'+xmlclip+'</track>' if name=='ref' else xmlclip)
        xml=(f'<xmeml version="5"><sequence><rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>'
             f'<media><audio>{tracks[0]}<track>{tracks[1]}{tracks[2]}</track></audio>'
             '</media></sequence></xmeml>').encode()
        project=Project(xml)
        code={'ref':1.0,'first':2.0,'second':3.0}
        def features(self, path, sec, duration):
            return np.full((120,5), code[Path(path).stem],dtype=np.float32)
        def match(a,b):
            ref, curr=int(a[0,0]), int(b[0,0])
            if ref==1 and curr==2: return (60.0,.90)
            if ref==1 and curr==3: return (20.0,.95)
            return None
        with patch('engine.AudioCache.features', features), patch('engine.correlate_features',match):
            result=sync(project, 'audio-1', threshold=.6)
        first=next(g for g in project.groups if g.title=='first')
        second=next(g for g in project.groups if g.title=='second')
        self.assertEqual(first.status, "synced")
        self.assertAlmostEqual(first.assigned, 60)
        self.assertEqual(second.status, "synced")
        self.assertAlmostEqual(second.assigned, 20)
        self.assertEqual(result['synced'],2)
