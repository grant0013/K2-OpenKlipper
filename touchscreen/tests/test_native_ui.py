"""Exercise the real LVGL tree, events and native geometry without hardware."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from test_service import Service, status, settings

ROOT=Path(__file__).resolve().parents[1]
BINARY=Path(os.environ.get('SCREEN_BINARY',ROOT/'out/host/openklipper-screen'))
ASSETS=ROOT/'assets/generated'


@unittest.skipUnless(BINARY.exists(),'Build the native application before UI tests')
class NativeUI(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)
  config=self.path/'native.cfg';config.write_text('[brand]\nmodel: Test printer\n[screen]\n')
  service=Service(config,mock.Mock());service.connected=True;service.status=status();service.settings=settings()
  service.status.update({'extruder':{'temperature':29,'target':0},'heater_bed':{'temperature':29,'target':0},
                         'temperature_sensor chamber_temperature':{'temperature':27},'hark_chamber':{'requested':0},
                         'gcode_move':{'position':[225,345,179.97],'gcode_position':[225,345,179.97],
                                       'speed_factor':1,'extrude_factor':1,'homing_origin':[0,0,0]},
                         'stepper_enable':{'steppers':{'stepper_x':True}},
                         'output_pin chamber_led':{'value':1},'fan':{'speed':0}})
  for letter in 'CD':service.status['hark_cfs_slots']['slots'][letter]={'loaded':True,'type':'PLA','name':'Generic PLA','color':'5035CE','nozzle_temp':220}
  service.status['hark_cfs_slots']['map_targets']=['A','B','C','D','E']
  service.status['k2_cfs'].update(active_slot='D',temperature=24,humidity=25)
  service.settings.update({'bed_mesh':{'mesh_min':[0,0],'mesh_max':[350,350]},'fan_generic auxiliary_fan':{},'fan_generic exhaust_fan':{},'resonance_tester':{},'k2_cfs':{},'k2_belt_tension':{}})
  service.materials=[{'name':'Generic PLA','type':'PLA','nozzle_temp':220},{'name':'Generic PETG','type':'PETG','nozzle_temp':250}]
  service.selected_file='part.gcode';service.metadata['part.gcode']={'estimated_time':7680,'filament_weight_total':64,'layer_count':500,'filaments':[{'tool':0,'type':'PLA','colour':'5035CE'}],'suggested_mapping':{'0':'D'}}
  service.files=[{'path':'part.gcode','modified':1700000000,'size':12345}]
  service.platform={'wifi':{},'timelapse':{'enabled':False},'free':'12 GB free','serial':'TEST','timezone':'America/Detroit'}
  self.data=service.snapshot()

 def render(self,page='home',actions=None,data=None):
  fixture=self.path/'fixture.json';fixture.write_text(json.dumps(data or self.data))
  action_file=self.path/'actions.json';action_file.write_text(json.dumps(actions or []))
  output=self.path/'screen.ppm';geometry=self.path/'geometry.json';record=self.path/'commands.jsonl'
  if record.exists():record.unlink()
  result=subprocess.run([str(BINARY),'--assets',str(ASSETS),'--fixture',str(fixture),'--page',page,
                         '--actions',str(action_file),'--record-commands',str(record),
                         '--snapshot',str(output),'--geometry',str(geometry)],capture_output=True,timeout=20)
  self.assertEqual(result.returncode,0,result.stderr.decode()+result.stdout.decode())
  commands=[json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []
  return json.loads(geometry.read_text()),commands

 def assert_rect(self,items,action,expected):
  actual=next(item['rect'] for item in items if item['action']==action)
  for got,want in zip(actual,expected):self.assertLessEqual(abs(got-want),1,(action,actual,expected))

 def test_all_pages_build_without_issuing_commands(self):
  pages=['home','move','heat','fans','filament','tune','calibration','files','file','mapping','mapping_pick',
         'settings','display','timers','refill','refill_sequence','network','ethernet','wifi','networks',
         'timelapse','recordings','storage','usb','system','machine','software','timezone','restart','save',
         'history','errors','help','language','objects','edit','materials','colors']
  for page in pages:
   with self.subTest(page=page):
    _,commands=self.render(page);self.assertTrue(all(c['action']=='files' for c in commands))

 def test_idle_home_has_no_history_button_and_exact_cfs_strip(self):
  items,_=self.render()
  self.assertFalse(any(item['action']=='route:history' for item in items))
  self.assert_rect(items,'home_filament_strip',[103,391,678,70])
  self.assert_rect(items,'open_files',[103,307,522,70])
  self.assert_rect(items,'home_fans',[717,307,64,70])
  self.assert_rect(items,'{"action":"light"}',[643,307,64,70])

 def test_cfs_strip_is_tappable_across_its_height(self):
  for point in ([110,398],[440,426],[600,426],[700,426],[772,454]):
   with self.subTest(point=point):
    items,commands=self.render(actions=[{'tap':point}])
    self.assertTrue(any(item['action']=='edit_slot' for item in items))
    self.assertEqual(commands,[])

 def test_filament_slots_and_footer_use_current_native_geometry(self):
  items,_=self.render('filament')
  expected={'slot:A':[103,117,121,202],'slot:D':[496,117,121,202],
            'slot:E':[655,117,121,202],'edit_slot':[147,398,110,68],
            'load':[269,398,190,68],'unload':[471,398,190,68],
            'route:refill':[673,398,64,68]}
  for action,rect in expected.items():self.assert_rect(items,action,rect)

 def test_actual_tab_touch_does_not_issue_commands_or_create_stray_controls(self):
  items,commands=self.render('move',[{'tap':[490,48]}])
  self.assertEqual(commands,[])
  self.assertTrue(any(item['action']=='edit_slot' for item in items))
  self.assertFalse(any(item['action'].startswith('jog:') for item in items))

 def test_subpage_buttons_have_touch_sized_targets(self):
  for page in ('move','heat','fans','filament','tune','calibration','files','file','mapping',
               'mapping_pick','settings','display','timers','refill','network','wifi',
               'storage','system','timezone','edit','materials','colors'):
   items,_=self.render(page)
   for item in items:
    with self.subTest(page=page,action=item['action']):
     self.assertGreaterEqual(min(item['rect'][2:]),52)

 def test_section_roots_use_sidebar_and_taller_tabs(self):
  for page in ('move','heat','fans','filament','tune','calibration','files','settings','network','timelapse','storage','system'):
   items,_=self.render(page)
   with self.subTest(page=page):
    self.assertFalse(any(i['action']=='back' for i in items))
    if page!='files':
     tabs=[i for i in items if i['action'].startswith('route:') and i['rect'][0]>=103 and i['rect'][1]==16]
     self.assertEqual(len(tabs),5 if page in ('settings','network','timelapse','storage','system') else 6)
     for item in tabs:self.assertEqual(item['rect'][3],64)

 def test_subpage_back_returns_to_previous_tab(self):
  for root,page in (('network','wifi'),('settings','display'),('files','file')):
   items,_=self.render(root,['route:'+page])
   self.assertTrue(any(i['action']=='back' for i in items))
   items,commands=self.render(root,['route:'+page,'back'])
   self.assertFalse(any(i['action']=='back' for i in items))
   self.assertTrue(all(c['action']=='files' for c in commands))

 def test_error_popup_has_one_centered_acknowledgment_without_restart(self):
  data=copy.deepcopy(self.data);data['error']='Example printer fault';data['status']['webhooks']['state']='error'
  items,commands=self.render(data=data)
  self.assertFalse(any(i['action'] in ('error_details','restart:firmware') for i in items))
  rect=next(i['rect'] for i in items if i['action']=='error_ack')
  self.assertLessEqual(abs(rect[0]+rect[2]/2-400),1)
  items,commands=self.render('move',['error_ack'],data=data)
  self.assertEqual(commands,[])
  self.assertFalse(any(i['action'].startswith('jog:') for i in items))

 def test_display_options_and_highlights_use_available_space(self):
  items,_=self.render('display')
  preferences=[i for i in items if i['action'].startswith('{') and json.loads(i['action']).get('action')=='preference']
  self.assertEqual(len(preferences),10)
  for item in preferences:
   payload=json.loads(item['action']);r=item['rect']
   self.assertGreaterEqual(r[3],76 if payload['key']=='accent' else 64)
   self.assertGreaterEqual(r[0],103);self.assertLessEqual(r[0]+r[2],781)
  for n,a in enumerate(preferences):
   for b in preferences[n+1:]:
    ar,br=a['rect'],b['rect']
    self.assertFalse(ar[0]<br[0]+br[2] and br[0]<ar[0]+ar[2] and ar[1]<br[1]+br[3] and br[1]<ar[1]+ar[3])

 def test_slider_knobs_fit_at_both_endpoints(self):
  from PIL import Image
  for speed in (0,1):
   data=copy.deepcopy(self.data);data['status']['fan']['speed']=speed
   self.render('fans',data=data);image=Image.open(self.path/'screen.ppm').convert('RGB')
   bright=[x for x in range(103,781) if min(image.getpixel((x,150)))>200]
   self.assertTrue(bright);self.assertGreater(min(bright),103);self.assertLess(max(bright),780)
   self.assertGreaterEqual(max(bright)-min(bright),28)

 def test_transient_preview_scenarios_render_actual_dialogs(self):
  import importlib.util
  spec=importlib.util.spec_from_file_location('demo_snapshot',ROOT/'developer-tools/demo_snapshot.py')
  demo=importlib.util.module_from_spec(spec);spec.loader.exec_module(demo)
  for name,action in [('error','error_ack'),('notice','notice_ack:1'),('recovery','plr:discard')]:
   items,commands=self.render(data=demo.preview_scenario(name))
   self.assertTrue(any(i['action']==action for i in items));self.assertEqual(commands,[])
  for name in ('loading','homing','meshing','load','unload'):
   data=demo.preview_scenario(name)
   self.assertIsInstance(data['operation'],dict)
   _,commands=self.render(data=data);self.assertEqual(commands,[])

 def test_move_targets_are_large_and_aligned(self):
  items,_=self.render('move')
  for action in ('jog:X:1','jog:Y:1','jog:Z:1','position:X','position:Y','position:Z'):
   r=next(i['rect'] for i in items if i['action']==action)
   self.assertGreaterEqual(r[2],72);self.assertEqual(r[3],76)
  step=next(i['rect'] for i in items if i['action']=='step:1')
  motors=next(i['rect'] for i in items if i['action']=='motor_toggle')
  self.assertEqual(step[1],motors[1]);self.assertEqual(step[3],motors[3])

 def test_outer_corners_are_opaque_dark_and_spinner_animates(self):
  from PIL import Image
  self.render()
  image=Image.open(self.path/'screen.ppm')
  for point in ((0,0),(799,0),(0,479),(799,479)):
   self.assertEqual(image.getpixel(point)[:3],(7,9,12))
  data=copy.deepcopy(self.data);data['operation']={'id':1,'label':'Homing','detail':'Homing axes'}
  self.render(data=data)
  initial=Image.open(self.path/'screen.ppm').crop((20,400,68,435)).copy()
  self.render(data=data,actions=[{}]*15)
  later=Image.open(self.path/'screen.ppm').crop((20,400,68,435))
  self.assertGreater(len(set(initial.tobytes()[i:i+3] for i in range(0,len(initial.tobytes()),3))),5)
  self.assertNotEqual(initial.tobytes(),later.tobytes())

 def test_wake_touch_is_consumed_before_navigation(self):
  data=copy.deepcopy(self.data);data['platform']['asleep']=True
  items,commands=self.render(data=data,actions=[{'tap':[43,281]}])
  self.assertTrue(any(i['action']=='open_files' for i in items));self.assertEqual(commands,[])
  awake=copy.deepcopy(data);awake['platform']['asleep']=False
  items,commands=self.render(data=data,actions=[{'tap':[43,281]},{'snapshot':awake},{'tap':[43,281]}])
  self.assertTrue(any(i['action'].startswith('file_open:') for i in items))
  self.assertEqual(commands,[{'action':'files','source':'gcodes'}])

 def test_temperature_layout_is_aligned_for_off_and_numeric_targets(self):
  for heater in ('extruder','heater_bed','chamber'):
   off,_=self.render(actions=['temperature:'+heater])
   on,_=self.render(actions=['temperature:'+heater,'temp_delta:5'])
   for action in ('temp_delta:-5','temp_input','temp_delta:5','temp_set','close'):
    before=next(i['rect'] for i in off if i['action']==action)
    after=next(i['rect'] for i in on if i['action']==action)
    self.assertEqual(before,after)
   left=next(i['rect'] for i in off if i['action']=='temp_delta:-5')
   middle=next(i['rect'] for i in off if i['action']=='temp_input')
   right=next(i['rect'] for i in off if i['action']=='temp_delta:5')
   self.assertEqual(left[1:],right[1:])
   self.assertEqual(left[1],middle[1]);self.assertEqual(left[3],middle[3])

 def test_manual_extrusion_uses_distance_without_cfs_load(self):
  data=copy.deepcopy(self.data);data['status']['extruder'].update(temperature=210,can_extrude=True)
  _,commands=self.render('move',['step:1',{'tap':[483,314]}],data=data)
  self.assertEqual(commands,[{'action':'extrude','value':1}])
  _,commands=self.render('move',['step:1',{'tap':[483,146]}],data=data)
  self.assertEqual(commands,[{'action':'extrude','value':-1}])
  items,_=self.render('move',data=data)
  self.assertTrue(any(i['action']=='{"action":"home","axis":"XY"}' for i in items))
  self.assertTrue(any(i['action']=='{"action":"home","axis":"Z"}' for i in items))

 def test_manual_extrusion_controls_refresh_when_nozzle_becomes_ready(self):
  cold=copy.deepcopy(self.data);cold['status']['extruder'].update(temperature=29,can_extrude=False)
  items,_=self.render('move',data=cold)
  self.assertFalse(any(i['action'].startswith('extrude:') for i in items))
  hot=copy.deepcopy(cold);hot['status']['extruder'].update(temperature=210,can_extrude=True)
  items,commands=self.render('move',[{'snapshot':hot}],data=cold)
  self.assertTrue(any(i['action']=='extrude:1' for i in items));self.assertEqual(commands,[])
  items,_=self.render('move',['step:100'],data=hot)
  self.assertFalse(any(i['action'].startswith('extrude:') for i in items))

 def test_temperature_cancel_and_valid_command(self):
  _,commands=self.render(actions=['temperature:extruder','number:Cancel'])
  # Direct close and the real Cancel button both dismiss without actuation.
  _,commands=self.render(actions=['temperature:extruder','close'])
  self.assertEqual(commands,[])
  _,commands=self.render(actions=['temperature:extruder','temp_input','number:3','number:5','number:0','number:Set'])
  self.assertEqual(commands,[{'action':'temperature','heater':'extruder','value':350}])

 def test_numeric_limits_reject_out_of_range_and_nan_text(self):
  _,commands=self.render(actions=['temperature:extruder','temp_input','number:3','number:5','number:1','number:Set'])
  self.assertEqual(commands,[])

 def test_unsaved_material_navigation_preserves_draft_until_discard(self):
  items,_=self.render('filament',['edit_slot','route:colors','color:5035ce','route:home'])
  self.assertTrue(any(item['action']=='confirmed' for item in items))
  items,commands=self.render('filament',['edit_slot','route:colors','color:5035ce','route:home','confirmed'])
  self.assertEqual(commands,[])
  self.assertTrue(any(item['action']=='open_files' for item in items))

 def test_mapping_defaults_and_back_preserve_selection(self):
  _,commands=self.render('file',['review_mapping','mapping_pick:0','mapping_set:A','back','review_mapping','start_print'])
  self.assertEqual(commands,[{'action':'start','path':'part.gcode','mapping':{'0':'A'},'calibrate':False}])

 def test_no_cfs_and_multiple_cfs_use_correct_layout_variants(self):
  data=copy.deepcopy(self.data);data['status']['k2_cfs'].update(fitted=False,active_slot=None)
  data['cfs_units']=0
  items,_=self.render('filament',data=data)
  self.assertFalse(any(item['action']=='units' for item in items))
  self.assertFalse(any(item['action']=='route:refill' for item in items))
  self.assertTrue(any(item['action']=='slot:E' for item in items))
  data=copy.deepcopy(self.data)
  for unit in (2,3):
   for letter in 'ABCD':data['slots'].append(dict(data['slots'][0],slot=str(unit)+letter))
  data['cfs_units']=3
  items,_=self.render('filament',data=data)
  self.assert_rect(items,'units',[103,314.44,114.19,60])

 def test_sidebar_files_refreshes_printer_storage(self):
  data=copy.deepcopy(self.data);data['source']='usb';data['platform']['usb']={'mounted':True}
  items,commands=self.render('home',['route:files'],data)
  self.assertEqual(commands,[{'action':'files','source':'gcodes'}])
  self.assertFalse(any(item['action'].startswith('files:') for item in items))

 def test_calibration_grid_never_overlaps_save(self):
  items,_=self.render('calibration')
  rects=[i['rect'] for i in items if i['action'].startswith('calibrate:')]
  for n,a in enumerate(rects):
   for b in rects[n+1:]:
    self.assertFalse(a[0]<b[0]+b[2] and b[0]<a[0]+a[2] and a[1]<b[1]+b[3] and b[1]<a[1]+a[3])

 def test_fan_slider_touch_sends_one_channel_request(self):
  _,commands=self.render('fans',[{'tap':[500,150]},'route:home'])
  self.assertEqual(len(commands),1)
  self.assertEqual(commands[0]['action'],'fan')
  self.assertEqual(commands[0]['fan'],'part')
  self.assertGreater(commands[0]['value'],0)
  self.assertLessEqual(commands[0]['value'],100)

 def test_timezone_offset_list_has_no_keyboard_or_overlapping_rows(self):
  data=copy.deepcopy(self.data);data['platform']['timezone']='Etc/GMT+5'
  items,commands=self.render('timezone',data=data)
  zones=[i for i in items if i['action'].startswith('timezone_pick:')]
  self.assertEqual(len(zones),37)
  self.assertFalse(any(i['action'].startswith(('timezone_search','region:')) for i in items))
  for a,b in zip(zones,zones[1:]):self.assertGreaterEqual(b['rect'][1],a['rect'][1]+a['rect'][3])
  selected=next(i for i in zones if i['action']=='timezone_pick:Etc/GMT+5')
  self.assertGreaterEqual(selected['rect'][1],114)
  self.assertLessEqual(selected['rect'][1]+selected['rect'][3],470)
  self.assertEqual(commands,[])

 def test_timezone_selection_waits_for_inline_apply_and_back_discards(self):
  data=copy.deepcopy(self.data);data['platform']['timezone']='Etc/GMT+5'
  items,commands=self.render('timezone',['timezone_pick:Europe/Athens'],data=data)
  self.assertEqual(commands,[])
  self.assertTrue(any(i['action']=='timezone_apply' for i in items))
  _,commands=self.render('timezone',['timezone_pick:Europe/Athens','timezone_apply'],data=data)
  self.assertEqual(commands,[{'action':'timezone','name':'Europe/Athens'}])
  _,commands=self.render('system',['route:timezone','timezone_pick:Europe/Athens','back'],data=data)
  self.assertEqual(commands,[])
  items,_=self.render('system',['route:timezone','timezone_pick:Europe/Athens','back','route:timezone'],data=data)
  self.assertFalse(any(i['action']=='timezone_apply' for i in items))

 def test_timezone_inline_apply_is_inside_selected_row_and_preserves_list_space(self):
  data=copy.deepcopy(self.data);data['platform']['timezone']='Etc/GMT+5'
  items,commands=self.render('timezone',['timezone_pick:Europe/Athens'],data=data)
  apply=next(i['rect'] for i in items if i['action']=='timezone_apply')
  selected=next(i['rect'] for i in items if i['action']=='timezone_pick:Europe/Athens')
  self.assertGreaterEqual(apply[0],selected[0]);self.assertLessEqual(apply[0]+apply[2],selected[0]+selected[2])
  self.assertGreaterEqual(apply[1],selected[1]);self.assertLessEqual(apply[1]+apply[3],selected[1]+selected[3])
  self.assertEqual(len([i for i in items if i['action']=='back']),1)
  fully_visible=[i for i in items if i['action'].startswith('timezone_pick:') and i['rect'][1]>=114 and i['rect'][1]+i['rect'][3]<=470]
  self.assertGreaterEqual(len(fully_visible),4)
  center=[apply[0]+apply[2]//2,apply[1]+apply[3]//2]
  _,commands=self.render('timezone',['timezone_pick:Europe/Athens',{'tap':center}],data=data)
  self.assertEqual(commands,[{'action':'timezone','name':'Europe/Athens'}])

 def test_timezone_drag_does_not_select_or_apply(self):
  data=copy.deepcopy(self.data);data['platform']['timezone']='Etc/GMT+5'
  gesture=[{'queue_touch':[300,y,True]} for y in (220,205,190,175,160)]
  gesture.append({'queue_touch':[300,160,False]})
  items,commands=self.render('timezone',gesture,data=data)
  self.assertEqual(commands,[])
  self.assertFalse(any(i['action']=='timezone_apply' for i in items))
  items,_=self.render('timezone',['timezone_pick:Europe/Athens'],data=data)
  r=next(i['rect'] for i in items if i['action']=='timezone_apply')
  x,y=r[0]+r[2]//2,r[1]+r[3]//2
  drag=[{'queue_touch':[x,y-offset,True]} for offset in (0,15,30,45,60)]
  drag.append({'queue_touch':[x,y-60,False]})
  _,commands=self.render('timezone',['timezone_pick:Europe/Athens']+drag,data=data)
  self.assertEqual(commands,[])

 def test_files_refresh_keeps_list_visible_and_navigation_available(self):
  from PIL import Image
  self.render('files');before=Image.open(self.path/'screen.ppm').crop((103,77,781,390)).copy()
  data=copy.deepcopy(self.data);data['files_refresh']={'busy':True,'error':''}
  data['operation']={'id':1,'action':'files','label':'Reading files'}
  self.render('files',data=data)
  after=Image.open(self.path/'screen.ppm').crop((103,77,781,390))
  self.assertEqual(before.tobytes(),after.tobytes())
  data['operation']=None
  items,commands=self.render('files',[{'snapshot':data},{'tap':[43,117]}],data=self.data)
  self.assertTrue(any(i['action']=='open_files' for i in items))
  self.assertTrue(all(c['action']=='files' for c in commands))

 def test_long_file_list_does_not_wrap_coordinates(self):
  data=copy.deepcopy(self.data);data['files']=[{'path':str(i)+'.gcode','modified':1} for i in range(150)]
  items,_=self.render('files',data=data)
  files=[i for i in items if i['action'].startswith('file_open:')]
  self.assertEqual(len(files),150)
  for a,b in zip(files,files[1:]):self.assertGreaterEqual(b['rect'][1],a['rect'][1]+a['rect'][3])

 def test_initial_printer_fault_is_visible_and_resolves_after_restart(self):
  fault=copy.deepcopy(self.data);fault['error']='Test shutdown';fault['status']['webhooks']['state']='shutdown'
  items,_=self.render(data=fault)
  self.assertTrue(any(item['action']=='error_ack' for item in items))
  items,_=self.render(actions=[{'snapshot':fault},{'snapshot':self.data}])
  self.assertFalse(any(item['action']=='error_ack' for item in items))
  self.assertTrue(any(item['action']=='open_files' for item in items))

 def test_home_sensor_and_light_widgets_update_from_live_snapshots(self):
  from PIL import Image
  self.render()
  before=Image.open(self.path/'screen.ppm').copy()
  changed=copy.deepcopy(self.data)
  changed['status']['k2_cfs'].update(temperature=31,humidity=42)
  changed['status']['output_pin chamber_led']['value']=0
  self.render(actions=[{'snapshot':changed}])
  after=Image.open(self.path/'screen.ppm')
  self.assertNotEqual(before.crop((510,391,780,461)).tobytes(),after.crop((510,391,780,461)).tobytes())
  self.assertNotEqual(before.crop((643,307,707,377)).tobytes(),after.crop((643,307,707,377)).tobytes())

 def test_snapshot_between_press_and_queued_release_does_not_lose_navigation(self):
  items,commands=self.render('files',[{'queue_touch':[43,117,True]},
     {'queue_touch':[43,117,False],'snapshot':self.data}])
  self.assertTrue(any(item['action']=='open_files' for item in items))
  self.assertTrue(all(c['action']=='files' for c in commands))

 def test_restart_requires_confirmation_even_during_print(self):
  data=copy.deepcopy(self.data);data['status']['print_stats']['state']='printing'
  for choice,action in (('klipper','restart'),('firmware','firmware_restart')):
   _,commands=self.render('restart',['restart:'+choice,'close'],data)
   self.assertEqual(commands,[])
   _,commands=self.render('restart',['restart:'+choice,'confirmed'],data)
   self.assertEqual(commands,[{'action':action,'confirm':True}])

 def test_failed_unload_notice_acknowledges_only_once(self):
  data=copy.deepcopy(self.data)
  data['notices']=[{'id':1,'title':'Unloading failed','detail':'Test failure','fault':True}]
  items,commands=self.render(actions=['notice_ack:1',{'snapshot':data}],data=data)
  self.assertEqual(commands,[{'action':'ack','id':1}])
  self.assertFalse(any(item['action']=='notice_ack:1' for item in items))

if __name__=='__main__':unittest.main()
