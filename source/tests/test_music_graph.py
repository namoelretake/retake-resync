"""Regression tests for live shows and pairwise multicamera matching."""
import unittest
import tempfile
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Project, sync

SR=16000
FPS=25

def write(path, x):
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x,-1,1)*32767).astype('<i2').tobytes())

def make_song(rng, length):
    """Synthesized long concert-like material: kick, clap, melody and room noise."""
    n=SR*length
    t=np.arange(n)/SR
    music=np.zeros(n,dtype=np.float32)
    bpm=106
    for beat in np.arange(0,length,60/bpm):
        index=int(beat*SR);duration=min(2800,n-index)
        if duration>0:
            b=np.arange(duration)/SR
            music[index:index+duration] += (0.42*np.exp(-b*25)*np.sin(2*np.pi*62*b)).astype('float32')
    for beat in np.arange(60/bpm, length,120/bpm):
        index=int(beat*SR);duration=min(1400,n-index)
        if duration>0:
            music[index:index+duration] += (0.17*np.exp(-np.arange(duration)/300)*rng.normal(size=duration)).astype('float32')
    melody=(np.sin(2*np.pi*220*t+0.9*np.sin(2*np.pi*0.38*t))*.10 +
            np.sin(2*np.pi*380*t+0.7*np.sin(2*np.pi*.13*t))*.08)
    music+=melody.astype('float32')
    music+=rng.normal(0,.025,size=n).astype('float32')
    return music

def node(track,cid,path,start,duration):
    c=ET.SubElement(track,'clipitem',id=cid)
    for key,value in [('name',cid),('start',start*FPS),('end',(start+duration)*FPS),('in',0),('out',duration*FPS)]:
        ET.SubElement(c,key).text=str(value)
    f=ET.SubElement(c,'file',id='f'+cid)
    ET.SubElement(f,'pathurl').text=path.as_uri()
    rate=ET.SubElement(f,'rate');ET.SubElement(rate,'timebase').text=str(FPS)

def mix(audio,rng):
    # Microphones with different low-pass properties and additive audience noise.
    filtered=.65*audio+.35*np.r_[audio[0], audio[:-1]]
    return filtered*.75 + rng.normal(0,.034,size=len(audio)).astype('float32')

class TestMusicGraph(unittest.TestCase):
    def test_second_song_with_no_original_camera_anchor(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);rng=np.random.default_rng(9871)
            song1=make_song(rng,46); song2=make_song(rng,48)
            sources={
                'A1':(song1[0:20*SR],0,'A'),
                'B1':(mix(song1[7*SR:29*SR],rng),0,'B'),
                'B2':(mix(song2[4*SR:28*SR],rng),25,'B'),
                'C2':(mix(song2[10*SR:33*SR],rng),0,'C'),
                'ISOLATED':(make_song(rng,14),55,'A')
            }
            root=ET.Element('xmeml',version='5');seq=ET.SubElement(root,'sequence')
            rate=ET.SubElement(seq,'rate');ET.SubElement(rate,'timebase').text=str(FPS)
            media=ET.SubElement(seq,'media');video=ET.SubElement(media,'video');tracks={x:ET.SubElement(video,'track') for x in ('A','B','C')}
            for cid,(sound,start,camera) in sources.items():
                path=d/(cid+'.wav');write(path,sound);node(tracks[camera],cid,path,start,len(sound)//SR)
            pr=Project(ET.tostring(root,encoding='utf-8'))
            result=sync(pr)
            status={g.title:g.status for g in pr.groups}
            for cid in ('A1','B1','B2','C2'):
                self.assertEqual(status[cid],'synced',result['groups'])
            self.assertEqual(status['ISOLATED'],'unmatched')
            self.assertEqual(result['verified_components'],2)
            self.assertEqual(result['unmatched'],1)
            self.assertAlmostEqual(next(g.assigned for g in pr.groups if g.title=='B1')-next(g.assigned for g in pr.groups if g.title=='A1'),7,delta=.08)
            self.assertAlmostEqual(next(g.assigned for g in pr.groups if g.title=='C2')-next(g.assigned for g in pr.groups if g.title=='B2'),6,delta=.08)
            out=ET.fromstring(pr.to_xml())
            self.assertEqual(out.find('.//clipitem[@id="ISOLATED"]/labels/label2').text,'Rose')

if __name__=='__main__':unittest.main()
