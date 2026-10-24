#!/bin/sh
# Records the Mac's own voice saying a voice note, for the app's self-test:
#   sh packaging/macos/speak.sh build/note.wav
# The listener has to hear "notes on", take down the note, and stop at "notes off".
set -e
out="$1"
note="${2:-remind me to buy milk tomorrow at nine}"
say -r 170 -o "$out" --file-format=WAVE --data-format=LEI16@16000 \
  "[[slnc 800]] Notes on. [[slnc 600]] $note. [[slnc 700]] Notes off. [[slnc 800]]"
echo "Recorded '$note' to $out"
