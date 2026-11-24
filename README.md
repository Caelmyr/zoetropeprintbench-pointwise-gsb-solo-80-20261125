# Zoetrope Print Bench

Plan and print the image ribbon for a tabletop zoetrope. A project contains the original frame images, the drum dimensions, an optional photo of the physical slit ring laid flat, and retained proofs made from particular draft revisions.

## Launch

Run `./start.sh` and visit http://127.0.0.1:19883/. **Try the reed bird sample** opens eight synthetic cut-paper frames and a synthetic backlit slit photo. To start your own drum, enter the measured circumference and artwork height in millimetres and import a sequence of frames or an evenly divided contact sheet. **Photo preparation** opens the integrated photo processor. A completed preparation can be added to the current drum as an input frame.

The `drum-library` Docker volume keeps drafts and retained proofs. `./start.sh --down` stops the service and retains that volume. Use `ZOEBENCH_PORT` with a separate Compose project to run an independent copy. For a native start, install the Python 3.11/3.12 lock and run `python -B -m zoebench`. `ZOEBENCH_DATA`, `HOST` and `PORT` set the data directory and listener.

## From artwork to a print

| Proof | What to look for |
| --- | --- |
| Print ribbon | Image cells with device-pixel starts, physical bounds, the assigned measured slit, center offset and gap, plus a blank join tab. |
| Cut guide | Separate guides for cutting and aligning the active band. |
| Slot ruler | Physical cut positions for the open slits each frame looks through: nominal slits by default, measured openings after a measured placement is adopted. |
| Direct frame loop | The selected print-framed artwork. The Play direct loop button advances the sequence at the selected RPM. |
| Slit-view proof | Predicted blur and edge contamination from the finite slit the selected frame actually looks through, one shutter interval, and any blank gap its opening catches. |
| Previous and next frame | An overlay of neighbouring silhouettes, including the wraparound pair. |
| Print clipping map | Ink cut by the cell and ink close to its quiet margin after each placement. |
| Loop motion trace | The visible ink centers and their path through the full sequence. |
| Proof tone inspection | Dark and light regions and the linear luminance distribution on the selected stock. |
| Measured slots | Widths, centers and incomplete intervals seen in the selected area of a backlit slit photograph. |
| Measured print positions | A recommended cell placement fitted to the measured openings, with the slit, center offset and gap for each cell. |
| Print scale target | A 100 mm line for verifying the actual print scale before cutting. |
| Join check | The active ends next to each other with the tab tucked underneath, plus the edge luminance difference. |

Click the saved proof to reopen its preserved overlay and readings. Download its PNG, the reading CSV where relevant, or a ZIP containing the exact input assets and the result. Print the ribbon, scale target and guides at actual size without fit-to-page.

When the slit ring is unevenly spaced, enter a uniform **cell width** under *Drum and paper*, then open *Slit photograph → Recommend print positions*. The fit keeps picture order from the join, places every cell center inside the projection of one measured opening (half a turn across the drum), refuses overlapping cells or cells that reach the join, and prefers centers close to the measured slit with inner gaps that stay as even as the openings allow. The panel lists the measured slit, the center offset and the remaining gap for each cell. If the photograph is incomplete or the openings cannot hold the cells, it names the opening or spacing that blocks the fit instead of falling back to an equal band. **Use measured positions** makes Print ribbon, Cut guide, Slot ruler, Slit-view proof and the per-frame views follow the fitted placement; **Back to nominal band** restores the equal band used to build a fresh drum. A worn placement is rejected, rather than silently redrawn, when the drum setup, slit photograph or picture count changes.

The bench accepts up to 24 single-image PNG, JPEG, TIFF or WebP sources per drum, up to 18 MiB and 12 megapixels each, with a 100 MiB frame-input limit. Two frames are required to make a band. The printable circumference range is 120–680 mm, subject to a minimum 8 mm cell pitch, and the device output uses 120–360 DPI. The simple optical model takes its position from the nominal slit circle and approximates one finite slit and shutter interval along a cylinder. The backlit photograph readout assumes its selected rectangle is a flat, front-on view with known horizontal span. The measurements describe the image and need a real scale and slit check before manufacture.
