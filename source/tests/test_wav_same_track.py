"""Regression: a Premiere track is not a unique recording source.

All cameras may appear on V1 and all their linked scratch audio and separate
WAV reference files may be cut into A1 without losing independent comparisons.
"""
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Project, sync

SR=16000
FPS=25

def write(path, values):
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(values,-1,1)*32767).astype('<i2').tobytes())

def clip(track, uid, path, start, duration, other=None):
    c=ET.SubElement(track,'clipitem',id=uid)
    for k,v in (('name',uid),('start',start*FPS),('end',(start+duration)*FPS),('in',0),('out',duration*FPS)):
        ET.SubElement(c,k).text=str(v)
    f=ET.SubElement(c,'file',id='f'+uid)
    ET.SubElement(f,'pathurl').text=path.as_uri()
    rate=ET.SubElement(f,'rate'); ET.SubElement(rate,'timebase').text=str(FPS)
    if other:
        l=ET.SubElement(c,'link'); ET.SubElement(l,'linkclipref').text=other

class TestWavSameTrack(unittest.TestCase):
    def test_multiple_wav_references_and_cameras_on_same_tracks(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            rng=np.random.default_rng(682)
            song=np.clip(rng.normal(0,.19,SR*43),-1,1).astype(np.float32)
            root=ET.Element('xmeml',version='5'); seq=ET.SubElement(root,'sequence')
            r=ET.SubElement(seq,'rate'); ET.SubElement(r,'timebase').text=str(FPS)
            media=ET.SubElement(seq,'media'); v=ET.SubElement(ET.SubElement(media,'video'),'track')
            a=ET.SubElement(ET.SubElement(media,'audio'),'track')
            cameras=[('camA',0,15,0),('camB',8,17,22),('camC',18,16,45)]
            for name,offset,length,timeline in cameras:
                path=d/f'{name}.wav'
                write(path,song[offset*SR:(offset+length)*SR] + rng.normal(0,.01,SR*length))
                clip(v,name+'_v',path,timeline,length,name+'_a')
                clip(a,name+'_a',path,timeline,length,name+'_v')
            refs=[('mixed1',2,22,76),('mixed2',7,25,100),('mixed3',16,23,128)]
            for name,offset,length,timeline in refs:
                path=d/f'{name}.wav'
                write(path,song[offset*SR:(offset+length)*SR])
                clip(a,name,path,timeline,length)
            p=Project(ET.tostring(root,encoding='utf-8'))
            self.assertEqual(len(p.groups),6)
            out=sync(p)
            self.assertGreater(out['comparisons'],0)
            statuses={g.title:g.status for g in p.groups}
            for name in ['camA_v','camB_v','camC_v','mixed1','mixed2','mixed3']:
                self.assertEqual(statuses[name],'synced',out['groups'])
            assigned={g.title:g.assigned for g in p.groups}
            self.assertAlmostEqual(assigned['camB_v']-assigned['camA_v'],8,delta=.08)
            self.assertAlmostEqual(assigned['camC_v']-assigned['camA_v'],18,delta=.08)
            # Premiere-compatible XML track rewriting must not lose any clip.
            xml=ET.fromstring(p.to_xml())
            self.assertEqual(len(xml.findall('.//clipitem')),9)

if __name__=='__main__':
    unittest.main()
