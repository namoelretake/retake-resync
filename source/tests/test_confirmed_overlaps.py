"""Regression tests: never count isolated reference footage as synchronized."""
import unittest
import xml.etree.ElementTree as ET
import tempfile
from pathlib import Path
import sys
import wave
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Project, sync


def wave_out(path, samples):
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000)
        f.writeframes((np.clip(samples,-1,1)*32767).astype('<i2').tobytes())


def add_clip(track, cid, path, pos_seconds, dur_seconds, link_id=None):
    c = ET.SubElement(track, 'clipitem', id=cid)
    for tag,value in [('name',cid),('start',int(pos_seconds*25)),('end',int((pos_seconds+dur_seconds)*25)),('in',0),('out',int(dur_seconds*25))]:
        ET.SubElement(c,tag).text=str(value)
    file=ET.SubElement(c,'file', id='f'+cid)
    ET.SubElement(file,'pathurl').text=Path(path).as_uri()
    rate=ET.SubElement(file,'rate');ET.SubElement(rate,'timebase').text='25'
    if link_id:
        link=ET.SubElement(c,'link');ET.SubElement(link,'linkclipref').text=link_id
    return c


def base_xml():
    root=ET.Element('xmeml', version='5')
    seq=ET.SubElement(root,'sequence')
    rate=ET.SubElement(seq,'rate');ET.SubElement(rate,'timebase').text='25'
    media=ET.SubElement(seq,'media');video=ET.SubElement(media,'video');audio=ET.SubElement(media,'audio')
    return root,video,audio


class TestConfirmedOverlaps(unittest.TestCase):
    def test_lonely_reference_take_is_red_and_sent_after_verified_pairs(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            rng=np.random.default_rng(133)
            source=rng.normal(0,.15,8000*24).astype(np.float32)
            distinct=rng.normal(0,.15,8000*16).astype(np.float32)
            p1=d/'camA_take01.wav'; p2=d/'camA_alone.wav';p3=d/'camB_take01.wav'
            wave_out(p1,source);wave_out(p2,distinct);wave_out(p3,source[5*8000:17*8000])
            root,video,audio=base_xml()
            v1=ET.SubElement(video,'track');v2=ET.SubElement(video,'track')
            a1=ET.SubElement(audio,'track');a2=ET.SubElement(audio,'track')
            add_clip(v1,'A_good_V',p1,0,24,'A_good_A')
            add_clip(a1,'A_good_A',p1,0,24,'A_good_V')
            add_clip(v1,'A_alone_V',p2,200,16,'A_alone_A')
            add_clip(a1,'A_alone_A',p2,200,16,'A_alone_V')
            add_clip(v2,'B_good_V',p3,0,12,'B_good_A')
            add_clip(a2,'B_good_A',p3,0,12,'B_good_V')
            project=Project(ET.tostring(root,encoding='utf-8'))
            self.assertEqual(len(project.groups),3)
            result=sync(project,'video-1')
            status={g.title:g.status for g in project.groups}
            self.assertEqual(status['A_good_V'],'synced')
            self.assertEqual(status['B_good_V'],'synced')
            self.assertEqual(status['A_alone_V'],'unmatched')
            self.assertEqual(result['unmatched'],1)
            self.assertEqual(result['isolated'],1)
            self.assertEqual(result['synced']+result['reference_groups'],2)
            good_end=max(g.assigned+g.duration for g in project.groups if g.status=='synced')
            alone=next(g for g in project.groups if g.title=='A_alone_V')
            self.assertAlmostEqual(alone.assigned,good_end+2,places=3)
            exported=ET.fromstring(project.to_xml())
            for cid in ('A_alone_V','A_alone_A'):
                self.assertEqual(exported.find(f'.//clipitem[@id="{cid}"]/labels/label2').text,'Rose')
            for cid in ('A_good_V','B_good_V'):
                self.assertIsNone(exported.find(f'.//clipitem[@id="{cid}"]/labels/label2'))

    def test_single_camera_with_linked_audio_is_not_false_synced(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d)
            rng=np.random.default_rng(129)
            p=d/'single.wav';wave_out(p,rng.normal(0,.17,8000*12))
            root,video,audio=base_xml()
            add_clip(ET.SubElement(video,'track'),'lonely_video',p,10,12,'lonely_audio')
            add_clip(ET.SubElement(audio,'track'),'lonely_audio',p,10,12,'lonely_video')
            project=Project(ET.tostring(root,encoding='utf-8'))
            self.assertEqual(len(project.groups),1)
            result=sync(project, 'auto')
            self.assertEqual(result['synced']+result['reference_groups'],0)
            self.assertEqual(result['unmatched'],1)
            self.assertEqual(result['isolated'],1)
            self.assertEqual(result['groups'][0]['status'],'unmatched')
            self.assertEqual(result['timeline'][0]['status'],'unmatched')
            exported=ET.fromstring(project.to_xml())
            self.assertEqual(exported.find('.//clipitem[@id="lonely_audio"]/labels/label2').text,'Rose')

if __name__ == '__main__': unittest.main()
