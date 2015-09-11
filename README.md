# aws-snapshot-tender
A better way to create and prune snapshots in AWS EC2.

## Introduction
EC2 Snapshots are an endless stream. Unlike real tapes, they don't get lost. The only things we need to configure are how often to take snapshots (recurrence) and which snapshots to keep as they age (retention).

We don't need to consider the type of snapshot--incremental versus full--because there's only one: incremental changed blocks. The only incentive to pruning is to cut snapshot storage costs. Otherwise we could take an infinite number of snapshots in the endless steam.

This tool cleanly separates the functions of creating and pruning.

### How it works:

 * Searches all volumes and instances in a region for a *Snapshot* tag.
 * *Snapshot* tags contain recurrence and retention policies.
 * A volume will inherit its policy from the attached instance, but this can be overridden on the volume.
 * *Recurrence*: How often is a snapshot required? If we don't have a recent one, create it.
 * *Retention*: Looking back in time, which snapshots should I keep?

### Advantages:

 * Retention is not driven by how frequently this script is run. Retention is written into the *Snapshot* tag.
 * Running this script more frequently means you will respond more quickly to the need for a new snapshot. It will not create more and more snapshots, and it will not delete too many snapshots.
 * All snapshots for a volume will be included in the retention calculation. So feel free to take additional snapshots at any time, such as just before a dangerous change or after an important update.
 * If you want your snapshot retention windows to be calculated from a strict boundary like 00:00 UTC, you can supply your own time.
 * If you accidentally run two copies of this script from different locations WITH ACCURATE CLOCKS, you won't mess up the results. The scripts will calculate the same. It's the same as running this script more frequently. If the clocks are significantly different, or one script is given a custom time parameter, then that may over-prune.


## Recurrence and Retention

Recurrence is simply "how often a snapshot is required". **aws-snapshot-tender** looks at the recurrence on each volume and instance. If enough time has passed, **aws-snapshot-tender** makes a request for a snapshot.

Retention can be visualized as a series of windows stretching back in time, starting from right now. The first window back in time might be for a day, then another window for a month. When these windows are laid over the timeline of snapshots, we see that some windows have more than one snapshot. Only the most recent snapshot in each window is kept. So to keep more snapshots, make the windows smaller.

Snapshot tags have just one element to define recurrence (starting with `@`) and additional elements for retention (starting with `+`)

Here's an example:
```script
@4h +4h6 +1d7 +1w4 +1m12
```

* Take a snapshot @ 4 hour intervals.
* Retain a snapshot every 4 hour interval, for 6 intervals.
* After that... Retain a snapshot every 1 day, for 7 days.
* After that... Every week for 4 weeks.
* After that... Every month for 12 months.
* Total retention time would be: 12 months + 4 weeks + 7 days + 24 hours. = 13.25 months.

### Ignore
To ignore volumes of a configured instance, or to temporarily ignore an instance, start the tag value with a hyphen.
* ``` ---DO NOT BACKUP--- ```

* ```  --   @2h +1h24 +1d7 +1w4 +1m36 ```


### Notes & Clarifications
* Tags with a recurrence but no retention will create snapshots that are never pruned.
* A volume with a defined snapshot tag will take precedence over an instance set to ignore.
* Be sure the recurrence is small enough. A retention of 1 day doesn't make sense with a recurrence of 1 week, because 6 one-day windows would be skipped waiting for another snapshot to be created.

## Requirements
**aws-snapshot-tender** was developed with the following versions.
* Python 2.7.9
* Boto 2.38.0
* ConfigArgParse 0.93

## Usage
```
usage: aws-snapshot-tender.py [-h] (--create | --prune) [--tag TAG]
                              [--time TIME] [-c CONFIG] -r REGIONS
                              [REGIONS ...] [--dry-run] [--profile PROFILE]
                              [--awsid AWSID] [--awskey AWSKEY]
                              [--logfile LOGFILE]

Args that start with '--' (eg. --create) can also be set in a config file
(/Users/jay/trimble/devops/operations/aws-snapshot-tender/aws-snapshot-
tender.py.conf or specified via -c) by using .ini or .yaml-style syntax (eg.
create=value). If an arg is specified in more than one place, then command-
line values override config file values which override defaults.

optional arguments:
  -h, --help            show this help message and exit
  --create              Create snapshots.
  --prune               Prune snapshots.
  --tag TAG             Tag to search for.
  --time TIME           Timestamp to use for comparison.
  -c CONFIG, --config CONFIG
                        Config file.
  -r REGIONS [REGIONS ...], --regions REGIONS [REGIONS ...]
                        Regions to connect to.
  --dry-run             Do not make changes.
  --profile PROFILE     Boto profile name.
  --awsid AWSID         AWS Access Key ID
  --awskey AWSKEY       AWS Secret Access Key
  --logfile LOGFILE     Logfile location. Default is current directory.
```
