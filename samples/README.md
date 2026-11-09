# Sample documents

- `printed_en.png` is the three-row delivery sheet from the brief. It is the first end-to-end demonstration sample.
- `printed_he.png` and `printed_bilingual.png` cover Hebrew right-to-left content and bilingual fields, respectively.
- `handwritten_en.png` contains three archival English ink lines. It crops Ralph Waldo Emerson's 21 July 1855 letter to Walt Whitman, a public-domain manuscript. [Scan and attribution](https://commons.wikimedia.org/wiki/File:Emerson%27s_Letter_to_Whitman.jpg).
- `handwritten_he.png` crops, resizes and pads the first line of HHD_age test image `w14_F_2_form19.tif`. This is actual Hebrew ink. It is based on HHD_age by Irina Rabaev and collaborators as credited by the dataset, used under CC BY 4.0. The reference transcription was checked directly against the crop, and reused for the same line in the mixed sample. [Dataset record](https://zenodo.org/records/14996257), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- `mixed_ink.png` places the same HHD_age crop in an English printed table to test mixed print and ink on one page. The composite shares the same attribution.
- `reviewed_export.csv` is the actual HTTP export from the printed English sample after manual review changes. It was imported into LibreOffice Calc.
- `unlabeled_inventory.png` has no header or table borders and leaves a middle cell blank. `low_light_blur.png`, `slight_rotation.png` and `blank.png` are controlled image-quality fixtures.
- `delivery_form.png`, `trip_form.png`, `inspection_form.png` and `equipment_form.png` are blank print-and-fill practice pages. Use them to create fresh physical samples for the phone run.

`expected.json` stores expected records, headings and transcripts for the supplied evaluation images. For a delivery form trial, print it and write the three rows in the table below. Keep that photo and its reference together before processing. In the trip log, inspection and equipment forms, use fictional values and write down the expected table before the trial.

| Vehicle | Driver | Destination | Delivery Time |
| --- | --- | --- | --- |
| 102 | Daniel | Haifa | 09:30 |
| 205 | Maya | Tel Aviv | 11:15 |
| 318 | Ron | Kiryat Shmona | 13:00 |

For a Hebrew handwriting trial, use the same numbers and replace the driver and destination pairs with דניאל / חיפה, מאיה / תל אביב and רון / קריית שמונה. Capture good light first, then separately change glare, shadow, page angle or handwriting style. Save the original image for side-by-side review.
