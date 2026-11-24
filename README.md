# Zoetrope Print Bench

Plan and print the image ribbon for a tabletop zoetrope. A project contains the original frame images, the drum dimensions, an optional photo of the physical slit ring laid flat, and retained proofs made from particular draft revisions.

## Launch

Run `./start.sh` and visit http://127.0.0.1:19883/. **Try the reed bird sample** opens eight synthetic cut-paper frames and a synthetic backlit slit photo. To start your own drum, enter the measured circumference and artwork height in millimetres and import a sequence of frames or an evenly divided contact sheet. **Photo preparation** opens the integrated photo processor. A completed preparation can be added to the current drum as an input frame.

The `drum-library` Docker volume keeps drafts and retained proofs. `./start.sh --down` stops the service and retains that volume. Use `ZOEBENCH_PORT` with a separate Compose project to run an independent copy. For a native start, install the Python 3.11/3.12 lock and run `python -B -m zoebench`. `ZOEBENCH_DATA`, `HOST` and `PORT` set the data directory and listener.

## From artwork to a print

| Proof | What to look for |
| --- | --- |
| Print ribbon | Image cells with device-pixel starts, physical bounds and a blank join tab. |
| Cut guide | Separate guides for cutting and aligning the active band. |
| Slot ruler | Physical cut positions for nominal open slits across from their assigned frames. |
| Direct frame loop | The selected print-framed artwork. The Play direct loop button advances the sequence at the selected RPM. |
| Slit-view proof | Predicted blur and edge contamination from a finite slit and one shutter interval. |
| Previous and next frame | An overlay of neighbouring silhouettes, including the wraparound pair. |
| Cross-revision lineage | A frame-by-frame correspondence between the current loop and a retained proof of an earlier shoot, anchored on one recognized frame. Each old frame is used at most once, unmatched frames are flagged, and pause-area look-alikes get pose-and-flow advice. |
| Print clipping map | Ink cut by the cell and ink close to its quiet margin after each placement. |
| Loop motion trace | The visible ink centers and their path through the full sequence. |
| Proof tone inspection | Dark and light regions and the linear luminance distribution on the selected stock. |
| Measured slots | Widths, centers and incomplete intervals seen in the selected area of a backlit slit photograph. |
| Print scale target | A 100 mm line for verifying the actual print scale before cutting. |
| Join check | The active ends next to each other with the tab tucked underneath, plus the edge luminance difference. |

Click the saved proof to reopen its preserved overlay and readings. Download its PNG, the reading CSV where relevant, or a ZIP containing the exact input assets and the result. Print the ribbon, scale target and guides at actual size without fit-to-page.

The bench accepts up to 24 single-image PNG, JPEG, TIFF or WebP sources per drum, up to 18 MiB and 12 megapixels each, with a 100 MiB frame-input limit. Two frames are required to make a band. The printable circumference range is 120–680 mm, subject to a minimum 8 mm cell pitch, and the device output uses 120–360 DPI. The simple optical model takes its position from the nominal slit circle and approximates one finite slit and shutter interval along a cylinder. The backlit photograph readout assumes its selected rectangle is a flat, front-on view with known horizontal span. The measurements describe the image and need a real scale and slit check before manufacture.
