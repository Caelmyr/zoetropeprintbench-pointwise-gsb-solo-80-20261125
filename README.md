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
| Print clipping map | Ink cut by the cell and ink close to its quiet margin after each placement. |
| Loop motion trace | The visible ink centers and their path through the full sequence. |
| Proof tone inspection | Dark and light regions and the linear luminance distribution on the selected stock. |
| Measured slots | Widths, centers and incomplete intervals seen in the selected area of a backlit slit photograph. |
| Print scale target | A 100 mm line for verifying the actual print scale before cutting. |
| Join check | The active ends next to each other with the tab tucked underneath, plus the edge luminance difference. |

Click the saved proof to reopen its preserved overlay and readings. Download its PNG, the reading CSV where relevant, or a ZIP containing the exact input assets and the result. Print the ribbon, scale target and guides at actual size without fit-to-page.

The bench accepts up to 24 single-image PNG, JPEG, TIFF or WebP sources per drum, up to 18 MiB and 12 megapixels each, with a 100 MiB frame-input limit. Two frames are required to make a band. The printable circumference range is 120–680 mm, subject to a minimum 8 mm cell pitch, and the device output uses 120–360 DPI. The simple optical model takes its position from the nominal slit circle and approximates one finite slit and shutter interval along a cylinder. The backlit photograph readout assumes its selected rectangle is a flat, front-on view with known horizontal span. The measurements describe the image and need a real scale and slit check before manufacture.

## Rebuilding a painted band from flat photos

If only photographed fragments of an earlier drum's picture band survive, **Old band reassembly** in the sidebar puts them back in the old frame order. Enter how many frames the band had, add up to six flat photos, then mark each photo: drag a rectangle around the visible strip and place two boundaries you recognise, giving each its old-band frame number. The two named numbers calibrate the strip, so overlapping photos (or photos shelved in the wrong order) still agree. A frame is only taken from a photo that shows the whole of it; a frame clipped by a photo edge stays a numbered gap rather than being stitched from two partial views.

**Review frame sources** shows every old-band slot with the photos that capture it completely. The recommendation picks one take per frame with a small dynamic programme that balances reproduced line clarity against the luminance step between neighbouring frames around the loop. Dropdowns on any slot let you choose another whole take (or leave the gap), and the adopted sequence records which photo each picture came from.

After adoption, select a frame under **Picture frames** to see its old-band slot, source photo, take rectangle and luminance/clarity readings. *Open original photo* downloads the untouched band photo, and *Show take on photo* overlays the exact crop rectangle on it. Play direct loop steps through the whole sequence at the configured RPM; uncaptured beats stay in their original slot and show a hatched "not captured" panel instead of letting later frames shift forward. The same gaps appear in the print ribbon, coverage, motion and neighbour proofs.
