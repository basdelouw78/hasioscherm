#!/bin/bash -e
# Kopieert de repo (door de workflow in files/hasioscherm gezet) naar de
# image en draait daar install.sh in de chroot.
install -d "${ROOTFS_DIR}/opt/hasioscherm-src"
cp -a files/hasioscherm/. "${ROOTFS_DIR}/opt/hasioscherm-src/"

on_chroot << CHEOF
HASIOSCHERM_CHROOT=1 /opt/hasioscherm-src/install.sh
rm -rf /opt/hasioscherm-src
CHEOF
