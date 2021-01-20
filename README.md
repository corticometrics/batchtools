# batchtools

Python-based Command Line Tools to interact with AWS Batch

## Setup
### First time install
It is suggested that you set up a virtual/conda environment for `batchtools`. 
Tools to interact with AWS require specific versions, which may conflict with other software on your system if not used in an isolated environment.
#### Optional (but recommended): environment setup

- download and setup conda if you don't have it installed. See their [Installation guide](https://conda.io/docs/user-guide/install/index.html)
- setup environment:
```
conda create -n batch python=3.6 pip
conda activate batch
```
**OR** use a python virtual environment
#### Install using pip
```
pip install batchtools
```

### Development
- clone this repo and cd into it:
```
git clone git@gitlab.corticometrics.com:internal_projects/batchtools.git
cd batchtools
```
- install the package within your new environment
```
pip install -e .
```
- To install additional dev requirements (as well as interactive programming tools, like `jupyterlab` and `ipython`), install the dev requirements from within the `batch` conda environment:
```
# make sure you're in this repo
cd /path/to/batchtools
# if not done already
conda activate batch
pip install -r requirements-dev.txt
```
- Before any commit, run the tool `black` to style code in a uniform format. This also includes any `versioneer` related code (used to create the version of the tool, which is auto-generated)
```
black --target-version py36 --exclude .*version.* .
```

## Usage
Currently supports two command line tools: `submit_subjects` and `check_status`. Both require AWS Batch to be properly configured, and these commands need to be ran by AWS users with appropriate IAM permissions to access Batch

### `submit_subjects`
Run with `--help` to see all options.

An example command would be:
```
submit_subjects \
  -q arn:aws:batch:us-east-1:123456789101:job-queue/my-queue \
  -j arn:aws:batch:us-east-1:123456789101:job-definition/my-definition:1 \
  -f /path/to/file_list \
  -o s3://my-bucket/path/to/output \
  -L /path/to/license_file.json  \
  -l /path/to/submission.json 
```
Where 
 - `/path/to/file_list` is  file with one s3:// path per line, pointing to a dicom dir (a folder containing just a single T1w DICOM series to be processed)
 - `/path/to/license_file.json` is a valid license provided by CorticoMetrics
 - `/path/to/submission.json` is a JSON log file created here, with information about submitted subjects

### `check_status`
This tool is still a work in progress, with more documentation coming soon! Run with `--help` to see all information

To continously check the status of subjects submitted above, run this command:
```
check_status \
 -l /path/to/submission.json \
 -s /path/to/status.json \
 --poll \
 --continuous
```
Where 
 - `/path/to/submission.json` is a JSON log file created by `ubmit_subjects`, with information about submitted subjects
 - `/path/to/status.json` is a JSON log file created here, with status information

After submitted subjects have completed processing, outputs can be downloaded locally using a `--get_*` command.
For example, to download all output from re:THINQ (log, report.pdf, subject_info.json, and artifact.tar.gz) from subjects that both succeeded and failed in processing:
```
check_status \
 --output_status both \
 -s /path/to/status.json \
 --save_location /path/to/results \
 --get_all \
 --ignore_nonexistant
```

## AWS Batch info (random notes)
on AWS Batch, there's a Dashboard where you can see what is running. It shows status of `SUBMITTED`, `PENDING`, `STARTING`, `RUNNING`, `SUCCESS` and `FAILURE`
https://console.aws.amazon.com/batch/home?region=us-east-1#/dashboard

we can create a Job Queue to submit to (currently using `reTHINQ-testing` ), which defines which Compute Environment(s) to use

The Compute Environment defines instance type, and max number of CPUs to run at once
you submit a job based on a Job Definition, which goes to a specific queue, and can have specified commands, env variables, etc (we currently use variables so the job can find the correct subject

within the Job Definition, the Container image to use is defined, the amount of resources the job needs is specified, and any AWS IAM roles can be assigned

as the job is running, you can click on it from within the dashboard, and there is a link to CloudWatch, where stdout/stderr are captured

status etc can be polled while the job is in the dashboard using `awscli` (or using `boto3`, the python SDK, from a script). this gives a json that will say its state,  a bunch of info on the job itself, and the `logStreamName` to find the job's logs in cloudwatch

I haven't played around with this part too much (mostly refer to the dashboard for course success/failure messages), and i remember cloudwatch being annoying in the past to access with the cli, but i think that may have improved recently

### Changing the number of max CPUS
- To change the number of CPUs and other features of the environment, go to the ["Compute environments" section of AWS BATCH](https://console.aws.amazon.com/batch/home?region=us-east-1#/compute-environments). 
- The `c5.4xlarge` is the recommended instance type. `reTHINQ` requires 16 vCPUs and 12 GB RAM to run
- Select the environment, and click "Edit". Make sure the "Service role" is set to `AWSBatchServiceRole` before saving any changes.
- If the number number of CPUs gets too high, the [EC2 Service Limits](https://console.aws.amazon.com/ec2/v2/home?region=us-east-1#Limits:) may need to be changed. Click on "Request limit increase" next to the instance type that you want to increase (default is `c4.4xlarge`), and fill out the "Service limit increase form". Be sure to select "Us East (Northern Virginia" as the region.


## kill all batch jobs
```
aws batch list-jobs --job-queue reTHINQ-testing --job-status $STATUS > file.json # default is RUNNING, if you skip this flag, need to do RUNNABLE, etc to kill everything
# open test.json in an editor, select all the JobIDs
# JOBS is all the JobIDs
for i in $JOBS; do
  aws batch terminate-job --job-id $i --reason "whatever"
done
```
