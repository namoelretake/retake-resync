import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import wave
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from engine import Project, sync


def write_wav(path, samples):
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(8000)
        f.writeframes(np.asarray(np.clip(samples, -1, 1) * 32767, dtype='<i2').tobytes())


def clip(cid, file, start, dur, link=None):
    elem = ET.Element('clipitem', id=cid)
    for tag, value in [('name', cid), ('start', start), ('end', start + dur), ('in', 0), ('out', dur)]:
        ET.SubElement(elem, tag).text = str(value)
    fil = ET.SubElement(elem, 'file', id='file-'+cid)
    ET.SubElement(fil, 'pathurl').text = Path(file).as_uri()
    rate = ET.SubElement(fil, 'rate')
    ET.SubElement(rate, 'timebase').text = '25'
    if link:
        ln = ET.SubElement(elem, 'link')
        ET.SubElement(ln, 'linkclipref').text = link
    return elem


class TestReSyncTail(unittest.TestCase):
    def test_unsynced_groups_are_moved_to_the_end_and_labeled_rose(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            rng = np.random.default_rng(898)
            source = rng.standard_normal(8000 * 28).astype('float32')*.16
            base, matched, noaudio = t/'base.wav', t/'matched.wav', t/'noaudio.wav'
            write_wav(base, source)
            write_wav(matched, source[8000*7:8000*19])
            write_wav(noaudio, np.zeros(8000*8))
            root = ET.Element('xmeml', version='5')
            seq = ET.SubElement(root, 'sequence')
            ET.SubElement(seq, 'name').text = 'ReSync teste'
            rate = ET.SubElement(seq, 'rate')
            ET.SubElement(rate, 'timebase').text='25'
            media = ET.SubElement(seq, 'media')
            v = ET.SubElement(media, 'video')
            a = ET.SubElement(media, 'audio')
            av1=ET.SubElement(v, 'track')
            av1.append(clip('base_v', base, 0, 28*25, 'base_a'))
            aa1=ET.SubElement(a, 'track')
            aa1.append(clip('base_a', base, 0, 28*25, 'base_v'))
            av2=ET.SubElement(v, 'track')
            av2.append(clip('match_v', matched, 0, 12*25, 'match_a'))
            av2.append(clip('fail_v', noaudio, 12*25, 8*25, 'fail_a'))
            aa2=ET.SubElement(a, 'track')
            aa2.append(clip('match_a', matched, 0, 12*25, 'match_v'))
            aa2.append(clip('fail_a', noaudio, 12*25, 8*25, 'fail_v'))
            project=Project(ET.tostring(root, encoding='utf-8'))
            self.assertEqual(len(project.groups),3)
            result=sync(project,'video-1',threshold=.60)
            self.assertEqual(result['synced'],1,result)
            self.assertEqual(result['unmatched'],1,result)
            match_group=next(g for g in project.groups if g.title=='match_v')
            fail_group=next(g for g in project.groups if g.title=='fail_v')
            self.assertAlmostEqual(match_group.assigned,7,delta=.08)
            self.assertGreaterEqual(fail_group.assigned,28+2)
            self.assertEqual(fail_group.status,'unmatched')
            self.assertEqual({c.new_start for c in fail_group.members},{fail_group.assigned})
            out=ET.fromstring(project.to_xml())
            for uid in ['fail_v','fail_a']:
                item=out.find(f'.//clipitem[@id="{uid}"]')
                self.assertEqual(item.findtext('./labels/label2'), 'Rose')
                self.assertIn('NÃO SINCRONIZADO',item.findtext('./comments/clipcommenta'))
                self.assertAlmostEqual(int(item.findtext('start'))/25,fail_group.assigned,delta=.04)
            self.assertEqual(len(list(out.iter('clipitem'))),6)
            self.assertEqual(len(result['timeline']),6)
            self.assertTrue(all(c['status']=='unmatched' for c in result['timeline'] if c['id'].startswith('fail')))
            self.assertEqual(project.to_xml(),project.to_xml())

if __name__=='__main__':unittest.main()
