# CMet data structure (CMeDS) format 
## Introduction
This document describes the neuroimage data structure called CMet data structure (CMeDS) format. Initially designed as a way to store data on the cloud to validate THINQ, it is a "simpler" alternative to BIDS format for storing subject metadata along with imaging data. Ideally, this should be capable of being transformed into BIDS format (though this may not be reversible)
## Definitions
- *`dataset`*: a top level directory that contains one or more *`image_set(s)`*. For example, this could be called 'validation', and contain all `image_set`s necessary for validation. A `dataset` should include a `README` with a description of any data stored within. A directory called `scripts` can be included to contain any code used to download, transform, analyze, etc the `dataset`. Ideally this should be source controlled with `git` and saved within GitLab.
- *`image_set`*: a directory containing a group of *`subjects`*, and their demographic information.  The `subjects` can all be from the same *`data_source`*, or from a variety of sources and need to be grouped together for other reasons (such as all images necessary for Cortical Accuracy validation). A `demographics.tsv` file is required, including a row for every `subject` in the `image_set`. An `image_set` should be treated as one data unit.
- *`subject`*: a directory containing imaging data and any necessary subject-specific metadata required for processing. This directory is named after a `subject_id` listed in the `image_set`'s `demographics.tsv`.

## Conventions
- Imaging data can be stored within a `subject` directory in either DICOM or NIfTI format. An `image_set` can have data in either or both formats.
- If the image data is in NIfTI format, then:
    - it must be stored as .nii.gz, to save disk space and to have a universal naming scheme
    - the T1w image used for processing must be caalled `<subject_id>.nii.gz`
    - a sidecar .json file, named `<subject_id>.json`, must exist which stores the metadata necessary for processing, as well as any optional information.
        - THINQ would normally extract these values from DICOM fields. The following are required for normative processing:
            - Age at scan
            - Sex
            - Scan manufacturer
            - Scanner field strength
    - if a manual segmentation file exists, it must be named `<subject_id>_seg_orig.nii.gz` and must be resampled into freesurfer 'orig' space (1mm isotropic), as this is the format output by THINQ and used for DICE comparison.
- If the image data is in DICOM format, then:
    - there must be a directory called `dicom`, containing only a single T1w DICOM series to be used for processing

### `image_set` details
- A `README` text file which provides notes on the originating source and anything special done to the files that is different from the original files (like a name change, or a resampling) can be included in the `image_set`.
- An image_set should include a directory called `scripts`, which contains any code used to download, transform, analyze, etc the `image_set`. Ideally this should be source controlled with `git` and saved within GitLab.
    - A file called `subjlist` that lists the `subject_id`s contained within an `image_set` must be stored in the `scripts` directory. scripts must use this file in their processing loops.
        - *NOTE:* Is this necessary, or should we just use the `demographics.tsv` as a single source of subject information
- `demographics.tsv` is the single source of truth for an `image_set`. There must be a one-to-one mapping of `subject_id`s listed in this file, to `subject` folders in the `image_set`. It must be possible to take this file as input to a script that processes all subjects within the `image_set`. If the `image_set` is composed of publicly available data, `demographics.tsv` can be based off of a csv or other information describing the data released by its curators, but it must be edited to only contain subjects with a `subject` folder in the `image_set`, and include data in all the required columns. Any number of optional columns can be included, but the following columns are required (case-sensitive, much match exactly):
    - `subject_id`: matches one of the `subject` directories
    - `age`: age at time of scan as a float value, required for normative model
    - `sex`: either `M` or `F`
    - `manufacturer`: manufacturer of the scanner used to acquire the data. either `Siemens`, `GE` or `Philips`
    - `field_strength`: field strength of the scanner used to acquire the data. either `1.5` or `3`
    - `diagnosis`: healthy controls (used for normative statistics) must be `HC`. Other diagnosis values will be standardized as needed, and are not included in created normative models
    - `file_type`: either `dicom` or `nifti`, matching the type of image used for that `subject`. necessary for processing `subject` with the correct container.
    - `source`: text describing where data comes from. could be the name of the publicly available dataset, for example, and could include additional details such as website. useful for data provenance. 
    - `scan_date`: date image was acquired in `%Y%m%d` format. If unknown, use `19700101`. Currently displayed on THINQ report, so want to display a value for cosmetic purposes only
    - `dob`: in `%Y%m%d` format. if unknown, subtract `age` from `scan_date` to get an approximate value. If both `scan_date` and `dob` are unknown, subtract `age` from `19700101`. Currently displayed on THINQ report, so want to display a value for cosmetic purposes only
- An optional file called `qc.tsv`can be included to record any notes on quality checking of the data either before or after processing. If a `subject_id` in the `image_set` is not listed in this file, it is assumed the data has not been quality checked but is not to be excluded from any further analysis. It must contain the following columns:
    - `subject_id`: matches `subject_id` of `demographics.tsv`
    - `rating`: either `pass`, `fail`, or empty
    - `reason`: must be included for failed, and could be blank if passed. comma separated list of:
        - `motion_major`: motion/ringing artifact effecting gm/wm contrast. rating must be `fail`
        - `motion_minor`: some minor motion, doesn't seem to effect contrast/segmentation. rating can be `pass` or `fail`
        - `timeout`: processing timed out. rating must be `fail`
        - `error`: processing fails due to error. rating must be `fail`
        - `bad_parc`: cortical parcellation/surfaces are poor. rating must be `fail`
        - `bad_seg`: wm/subcortical segmentations are poor. rating must be `fail`
        - `wrap_major`: wrapping of image into brain. rating must be `fail`
        - `wrap_minor`: wrapping of image, not into brain. rating can be `pass` or `fail`
        - `finding`: something may look unique or weird about this image or its processed results. rating can be `pass` or `fail`
        - `excluded`: excluded for a non image related reason (such as being recalled by data curators, or included in a validation set). rating must be `fail`.
    - `notes`: text describing the `reason`. must be present if `reason` is given
