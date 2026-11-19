// =====================================================================================
//  hasioscherm - inbouwbak voor Raspberry Pi Touch Display 2 (10"), liggend, in een
//  stenen muur. Het GLAS LIGT OP DE GESTUCTE MUUR: alleen de behuizing aan de achterkant
//  gaat de muur in. Van voren zie je alleen het glas.
//
//  Gebaseerd op het principe van het ontwerp dat Bas aanleverde (flush mount voor een
//  gipswand: gat ter grootte van de behuizing, bak met 4 paaltjes, afstandsbussen ("pegs")
//  die op het scherm geschroefd worden en over de paaltjes schuiven). Aangepast voor steen:
//  de bak wordt ingemetseld/ingegipst en vormt met zijn voorrand de exacte opening, gelijk
//  met het stucwerk. Het glas valt over die rand heen.
//
//  Printbaar op een bed van 220 x 220 mm (Ender 3 V3), PLA, zonder support.
//
//  Onderdelen (kies met 'deel', of gebruik frame/maak-stl.sh):
//    "bak_links", "bak_rechts"  - de twee helften van de bak (printligging)
//    "peg"                      - afstandsbus, 4x printen (printligging)
//    "samenstelling"            - scherm op de muur (alleen om te bekijken)
//    "doorsnede"                - bak + scherm doorgesneden
//    "maten"                    - toont de belangrijkste maten in de console
//
//  Assen: X = breedte (liggend), Y = hoogte, Z = diepte. Z = 0 is het gestucte
//  muuroppervlak (= achterkant glas), negatief = de muur in.
// =====================================================================================

deel = "samenstelling";

/* [Scherm (meten!)] */
glas_b      = 248.1;  // buitenmaat glas, liggend
glas_h      = 166.9;
glas_t      = 2;      // dikte glas (ligt op de muur, steekt zoveel uit)
scherm_d    = 15.5;   // totale dikte scherm
behuizing_b = 236.2;  // behuizing achter het glas (uit het aangeleverde sjabloon: 236,23 x 148,36)
behuizing_h = 148.4;
gat_x       = 160;    // afstand bevestigingsgaten achterkant scherm, liggend (uit aangeleverde bak: 160 x 121,8)
gat_y       = 121.8;

/* [Bak] */
speling    = 0.5;     // rondom tussen behuizing en voorrand van de bak
wand       = 2.4;
diepte_bin = 52;      // binnendiepte vanaf het muuroppervlak (scherm + Pi + PoE-HAT + koeler)
bodem      = 2;
paal_d     = 7;       // paaltjes waar de pegs overheen schuiven
paal_spel  = 2;       // ruimte tussen achterkant scherm en bovenkant paal
stucflens  = 8;       // flens rondom voor grip in het stucwerk/de specie
stucflens_z = -4;     // flens ligt 4 mm onder het oppervlak
ventgat_achter = 40;  // uitdrukbare gaten voor luchtkanaal/kabel (achterwand)
ventgat_zij    = 32;  // idem in de zijwanden

/* [Peg (afstandsbus)] */
peg_d      = 10;
peg_bin    = 7.1;     // schuift over paal_d met wat speling (krap = meer klemkracht)
peg_overlap = 15;     // zover schuift de peg over het paaltje
schroef_d  = 2.7;     // M2.5 door de dichte kant, in de gaten achter op het scherm
peg_kop    = 2;

$fn = 64;

// ---------------- afgeleide maten ----------------
bin_b = behuizing_b + 2 * speling;
bin_h = behuizing_h + 2 * speling;
bak_b = bin_b + 2 * wand;
bak_h = bin_h + 2 * wand;
z_scherm_achter = -(scherm_d - glas_t);           // achterkant behuizing
z_bodem = -diepte_bin - bodem;
z_paal_top = z_scherm_achter - paal_spel;
peg_l = paal_spel + peg_overlap + peg_kop;
paal_pos = [for (sx = [-1, 1]) for (sy = [-1, 1]) [sx * gat_x/2, sy * gat_y/2]];

// ---------------- bak ----------------
module bak() {
  difference() {
    union() {
      difference() {
        translate([0, 0, z_bodem]) linear_extrude(-z_bodem) square([bak_b, bak_h], center = true);
        translate([0, 0, z_bodem + bodem]) linear_extrude(-z_bodem) square([bin_b, bin_h], center = true);
      }
      // stucflens rondom (onderkant 45 graden: printbaar)
      difference() {
        hull() {
          translate([0, 0, stucflens_z - 2]) linear_extrude(2) square([bak_b + 2 * stucflens, bak_h + 2 * stucflens], center = true);
          translate([0, 0, stucflens_z - 2 - stucflens]) linear_extrude(0.01) square([bak_b, bak_h], center = true);
        }
        translate([0, 0, -100]) linear_extrude(200) square([bak_b - 0.02, bak_h - 0.02], center = true);
      }
      // paaltjes met voet
      for (p = paal_pos) translate([p[0], p[1], z_bodem + bodem - 0.01]) {
        cylinder(d = paal_d, h = z_paal_top - z_bodem - bodem);
        cylinder(d1 = paal_d + 8, d2 = paal_d, h = 4);
      }
    }
    // grip-gaten in de flens
    for (i = [-5 : 5]) for (s = [-1, 1]) translate([i * 21, s * (bak_h/2 + stucflens/2), -30]) cylinder(d = 4, h = 40);
    for (j = [-2 : 2]) for (s = [-1, 1]) translate([s * (bak_b/2 + stucflens/2), j * 25, -30]) cylinder(d = 4, h = 40);
    // uitdrukbare ventilatie-/kabelgaten: 0,6 mm vlies blijft staan
    for (x = [-60, 60]) translate([x, 0, z_bodem + 0.6]) cylinder(d = ventgat_achter, h = bodem);
    for (s = [-1, 1]) for (z = [-34]) translate([s * (bak_b/2 - (wand - 0.6)/2), 0, z]) rotate([0, 90, 0]) cylinder(d = ventgat_zij, h = wand - 0.6 + 0.02, center = true);
    for (s = [-1, 1]) translate([60, s * (bak_h/2 - (wand - 0.6)/2), -34]) rotate([90, 0, 0]) cylinder(d = ventgat_zij, h = wand - 0.6 + 0.02, center = true);
  }
}

// ---------------- peg ----------------
module peg() {
  difference() {
    cylinder(d = peg_d, h = peg_l);
    translate([0, 0, peg_kop]) cylinder(d = peg_bin, h = peg_l);
    translate([0, 0, -1]) cylinder(d = schroef_d, h = peg_kop + 2);
    // kleine inkeping zodat de peg wat veert en klemt
    translate([0, 0, peg_l - 8]) cube([1.2, peg_d + 1, 16], center = true);
  }
}

// ---------------- in helften voor een bed van 220 mm ----------------
module helft(links) { intersection() { children(); translate([links ? -500 : 0, -500, -200]) cube([500, 1000, 400]); } }
module bak_helft(links) {
  difference() {
    helft(links) bak();
    for (y = [-50, 50]) translate([0, y, z_bodem + bodem/2]) rotate([0, 90, 0]) cylinder(d = 1.95, h = 16, center = true, $fn = 16);
    for (s = [-1, 1]) for (z = [z_bodem + 10, -12]) translate([0, s * (bin_h/2 + wand/2), z]) rotate([0, 90, 0]) cylinder(d = 1.95, h = 16, center = true, $fn = 16);
  }
}
module print_bak(links) { translate([0, 0, -z_bodem]) bak_helft(links); }

// ---------------- voor de afbeeldingen ----------------
module scherm() {
  color("#111") translate([0, 0, 0]) linear_extrude(glas_t) square([glas_b, glas_h], center = true);
  color("#2a3550") translate([0, 0, glas_t]) linear_extrude(0.05) square([217.6, 136.4], center = true);
  color("#333") translate([0, 0, z_scherm_achter]) linear_extrude(-z_scherm_achter) square([behuizing_b, behuizing_h], center = true);
  color("#c9a227") for (p = paal_pos) translate([p[0], p[1], z_scherm_achter - peg_l]) peg();
  color("#1d7a3a") translate([0, 0, z_scherm_achter - 4]) cube([85, 56, 2], center = true);   // Pi 5 (indicatief)
}
module muur() {
  color("#ece7df") translate([0, 0, -30]) difference() {
    cube([glas_b + 220, glas_h + 160, 60], center = true);
    cube([bin_b, bin_h, 61], center = true);
  }
}

if (deel == "bak_links")  print_bak(true);
if (deel == "bak_rechts") print_bak(false);
if (deel == "peg") peg();
if (deel == "samenstelling") { muur(); scherm(); }
if (deel == "doorsnede") intersection() {
  union() { color("#9aa3ad") bak(); scherm(); }
  translate([-500, -gat_y/2, -200]) cube([1000, 500, 400]);
}
if (deel == "bak") color("#9aa3ad") bak();
if (deel == "maten") echo(bak_b = bak_b, bak_h = bak_h, bak_d = -z_bodem, flens_b = bak_b + 2 * stucflens, flens_h = bak_h + 2 * stucflens, opening_b = bin_b, opening_h = bin_h, overstek_lr = (glas_b - bin_b)/2, overstek_ob = (glas_h - bin_h)/2, peg_l = peg_l);
