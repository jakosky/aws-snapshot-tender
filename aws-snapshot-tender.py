#!/usr/bin/env python

'''
Copyright (c) 2015 Jay Jakosky

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
'''

import os
import boto.ec2
import configargparse
import logging
from datetime import datetime, timedelta
import re
from collections import defaultdict

superstructure = []

def ec2_data_into_superstructure(region, credentials, tagname):
  ''' Fetch volumes, instances and snapshots from EC2.
      For each volume, instantiate a combined volume-instance-snapshot structure.
      Add this to a global list.
  '''
  global superstructure
  superstructure = []
  conn = boto.ec2.connect_to_region(region, **credentials)
  snapshots = conn.get_all_snapshots(owner='self')
  logging.info("Snapshots: Fetched %s snapshots." % len(snapshots))
  volumes = conn.get_all_volumes()
  logging.info("Volumes: Fetched %s volumes." % len(volumes))
  instances = conn.get_only_instances()
  logging.info("Instances: Fetched %s instances." % len(instances))

  instancedict = {instance.id : instance for instance in instances}
  snapshotdict = defaultdict(list)
  for snapshot in snapshots:
    snapshotdict[snapshot.volume_id].append(snapshot)

  for vol in volumes:
    instance = None
    try:
      attachment_state = vol.attachment_state() if vol.attachment_state() else ''
      if attachment_state[:6] == 'attach':
        instance_id = vol.attach_data.instance_id
        instance = instancedict[instance_id]
    except: pass
    snaps = None
    try:
      snaps = snapshotdict[vol.id] 
    except: pass
    superstructure.append(VolInstSnapStruct(vol, instance, snaps, tagname))

class VolInstSnapStruct:
  ''' This class represents a single volume, plus a possible instance to which it is attached, plus all of the volume's snapshots.
  '''
  def __init__(self, volume, instance, snapshots, tagname):
    self.instance_id = None
    self.instance_name = None
    self.tag = None
    self.tagsource = None
    self.recurrence = None
    self.retention = None

    self.volume = volume
    self.instance = instance
    self.snapshots = snapshots

    self.volume_id = self.volume.id

    try:
      self.instance_id = self.instance.id
    except: pass

    try:
      self.instance_name = self.instance.tags['Name']
    except: pass

    try:
      self.tag = (self.volume).tags[tagname]
      self.tagsource = self.volume_id
    except:
      try:
        self.tag = (self.instance).tags[tagname]
        self.tagsource = self.instance_id
      except:
        pass

    if self.find_recurrence(self.tag):
      self.recurrence = self.find_recurrence(self.tag)
      logging.info("%s: found recurrence '%s' from %s" %(self.volume_id, self.tag, self.tagsource))
    if self.find_retention(self.tag):
      self.retention = self.find_retention(self.tag)
      logging.info("%s: found retention '%s' from %s" %(self.volume_id, self.tag, self.tagsource))

  def has_recurrence(self):
    return True if self.recurrence else False

  def has_retention(self):
    return True if self.retention else False

  def is_eligible(self):
    is_eligible = True
    try:
      if self.tag[0] == '-':
        is_eligible = False
    except:
      pass
    return is_eligible

  def find_recurrence(self,tag):
    ''' Returns: count, period
    '''
    try:
      match = re.findall('^\s*\@([1-9]+[0-9]*)([hdwm])',tag)
    except:
      return None
    if len(match)==1:
      return match[0]
    else:
      return None

  def find_retention(self,tag):
    ''' Returns: count, period, repeat
    '''
    try:
      matches = re.findall('\+([1-9]+[0-9]*)([hdwm])([1-9]*[0-9]*)',tag)
    except:
      return None
    if len(matches)>0:
      return matches
    else:
      return None

  def recent_snapshots(self,cutoff):
    ''' Search snapshot dictionary for the volume id, and check for any snapshots that are more recent than the cutoff date.
    '''
    recent_snapshots = []
    for snapshot in self.snapshots:
      start_time = datetime.strptime(snapshot.start_time, '%Y-%m-%dT%H:%M:%S.%fZ')
      if start_time > cutoff: #if snapshot is more recent than cutoff
        recent_snapshots.append(snapshot)
    return recent_snapshots if len(recent_snapshots)>0 else None

  def create_snapshot(self, description):
    self.volume.create_snapshot(description)

  def match_snapshots_to_windows(self,windows):
    ''' Given a list of time windows, match the snapshots to any applicable time windows.
    '''
    matched_windows = defaultdict(list)
    for snapshot in self.snapshots:
      start_time = datetime.strptime(snapshot.start_time, '%Y-%m-%dT%H:%M:%S.%fZ')
      for recent, distant in windows:
        if distant < start_time < recent:
          logging.info("%s: Window (%s, %s) contains %s (%s)." % (snapshot.volume_id, recent, distant, snapshot.id, start_time))
          matched_windows[(recent, distant)].append(snapshot)
    return matched_windows
  ## End class.


def cutoff_dt(dt, period, count):
  count = int(count)
  mcount = count-1 if count>0 else 0
  compare = {
          "h": dt-timedelta(hours=int(count)),
          "d": dt-timedelta(days= int(count)),
          "w": dt-timedelta(days= 7 * int(count)),
          "m": datetime(dt.year-int(mcount)//12, dt.month-int(mcount)%12, 1)-timedelta(milliseconds=1)
      }
  return compare.get(period, None)

def convert_to_windows(time, retention):
  windows = []
  recent = time
  for count, period, repeat in retention:
    repeat = int(repeat) if len(repeat)>0 else 1
    for i in range(1, repeat+1):
      distant = cutoff_dt(recent, period, 1)
      windows.append((recent, distant))
      recent = distant
  return windows

def create_snapshots(time, dry_run):
  ''' Find volumes that require a new snapshot.
      Recurrence timing is defined in the first element of a volume tag.
      Compare the age of the existing snapshots with the recurrence timing.
  ''' 

  logging.info("Timestamp for comparison: %s" % time)
  create_requests = []
  logging.info("Searching for volumes to snapshot...")
  for v in superstructure:
    recurrence_found = False
    count = None
    period = None
    if v.is_eligible():
      if v.has_recurrence():
        logging.info("%s: Has recurrence '%s'." % (v.volume_id,v.tag))
        count,period = v.recurrence
        cutoff = cutoff_dt(time, period, count)
        if v.recent_snapshots(cutoff):
          logging.info("%s has recent snapshot %s prior to cutoff %s." % (v.volume_id,[str(snap.id) for snap in v.recent_snapshots(cutoff)],cutoff))
        else:
          logging.info("Snapshot required for %s." % v.volume_id)
          create_requests.append(v)
    else:
      logging.info("%s is not eligible." % v.volume_id)
  logging.info("Search complete.")
  if dry_run:
    logging.info("Dry run. No create requests.")
  else:
    for v in create_requests:
      logging.info("Creating snapshot for %s from %s" % (v.volume_id, v.instance_id))
      v.create_snapshot("%s %s" % (v.volume_id, v.instance_id))


def prune_snapshots(time, dry_run):
  delete_requests = []
  logging.info("Searching for snapshots to prune...")
  for v in superstructure:
    windows = None
    if v.has_retention():
      logging.info("%s: Has retention '%s'." % (v.volume_id,v.tag))
      windows = convert_to_windows(time, v.retention)
      logging.info("%s: Matching snapshots to windows." % v.volume_id)
      matched_windows = v.match_snapshots_to_windows(windows)
      for key,snaps in matched_windows.iteritems():
        snaps.sort(key=lambda snap: datetime.strptime(snap.start_time, '%Y-%m-%dT%H:%M:%S.%fZ'))
        try:
          snaps.pop() # Remove highest-date snapshot, which is the most recent in the window.
        except:
          pass
        if len(snaps)>0:
          for snap in snaps:
            logging.info("%s: Pruning eligible for %s from %s '%s'" % (v.volume_id, snap.id, v.instance_id, v.instance_name))
            delete_requests.append(snap)
  logging.info("Search complete.")
  if dry_run:
    logging.info("Dry run requested. No delete requests.")
  else:
    for snapshot in delete_requests:
      logging.info("Deleting %s" % snapshot.id)
      try:
        snapshot.delete()
      except: pass


if __name__ == "__main__":
  p = configargparse.ArgParser(
    default_config_files=[os.path.abspath(__file__)+'.conf'])
  group = p.add_mutually_exclusive_group(required=True)
  group.add_argument('--create', action="store_true", help="Create snapshots.")
  group.add_argument('--prune', action="store_true", help="Prune snapshots.")
  p.add('--tag', required=False, help='Tag to search for.', default='Snapshot')
  p.add('--time', required=False, help='Timestamp to use for comparison.', default=datetime.utcnow())
  p.add('-c', '--config', required=False, help='Config file.',
    is_config_file=True)
  p.add('-r', '--regions', required=True, nargs='+', help='Regions to connect to.')
  p.add('--dry-run', required=False, action="store_true", help='Do not make changes.')
  p.add('--profile', required=False, help='Boto profile name.')
  p.add('--awsid', required=False, help='AWS Access Key ID')
  p.add('--awskey', required=False, help='AWS Secret Access Key')
  p.add('--logfile', required=False, help='Logfile location. Default is current directory.',
                     default=os.path.abspath(__file__)+'.log')
  args = p.parse_args()

  logging.basicConfig(level=logging.DEBUG,
                      format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                      datefmt='%y-%m-%d %H:%M',
                      filename=args.logfile,
                      filemode='w')
  console = logging.StreamHandler()
  console.setLevel(logging.INFO)
  formatter = logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s')
  console.setFormatter(formatter)
  logging.getLogger('').addHandler(console)

  credentials = {}
  if args.awsid:
    credentials['aws_access_key_id']=args.awsid
    credentials['aws_secret_access_key']=args.awskey
  elif args.profile:
    credentials['profile_name']=args.profile

  for region in args.regions:
    logging.info("Fetching volumes, instances and snapshots for region %s ..." % region)
    ec2_data_into_superstructure(region, credentials, args.tag)
    if args.create:
      create_snapshots(args.time, args.dry_run)
    elif args.prune:
      prune_snapshots(args.time, args.dry_run)

