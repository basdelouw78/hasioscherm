#!/bin/bash
# Maakt de STL-bestanden, afbeeldingen en de uitslijpmal opnieuw na een wijziging in hasioscherm-frame.scad.
set -e
cd "$(dirname "$0")"
mkdir -p stl afbeeldingen
for d in bak_links bak_rechts peg; do
  echo "STL: $d"; openscad -q -o "stl/$d.stl" -D "deel=\"$d\"" hasioscherm-frame.scad
done
R="openscad"; command -v xvfb-run >/dev/null && R="xvfb-run -a openscad"
$R -q -o afbeeldingen/samenstelling-voor.png -D 'deel="samenstelling"' --imgsize=1600,1100 --camera=0,0,0,30,0,10,640 --colorscheme=Tomorrow hasioscherm-frame.scad
$R -q -o afbeeldingen/doorsnede.png -D 'deel="doorsnede"' --imgsize=1600,1100 --camera=0,0,-25,65,0,25,420 --colorscheme=Tomorrow hasioscherm-frame.scad
$R -q -o afbeeldingen/bak.png -D 'deel="bak"' --imgsize=1600,1100 --camera=0,0,-25,35,0,20,480 --colorscheme=Tomorrow hasioscherm-frame.scad
python3 maak_uitslijpmal.py
