# d7print

## About
This is repo mostly serves as a documentation of what I have done to run my own control software on Wanhao d7+ printer.
It expects some experience with linux (preferably arch) and AVR development (or at least some knowledge of grbl).

## Expected hardware modifications
* External USB Wi-Fi adapter (DEXP WFA-151 in my case)
* Hardwired GPIO from Nanopi to Controller board (usb-b connector is not used):
  * 32 (GPIOA7) -> AtMega2560 ISP Reset
  * 36 (UART3_TX) -> AtMega2560 Pin2 (RXD0) - through desoldered 0603 1k smd resistor previously connecting it to AtMega16u2
  * 40 (UART3_RX) -> AtMega2560 Pin1 (TXD0) - through desoldered 0603 1k smd resistor previously connecting it to AtMega16u2
* Optional modifications:
  * Led projector replaced with https://www.aliexpress.com/item/33003036160.html
  * Led control mosfet gate/source hardwired to Led driver +/- PWM input
  * Extra 24V->12V buck converter to feed the box from single external 24V/5A power supply
  
## Flashing grbl to wanhao control board.
* Clone customized grbl-Mega from git@github.com:dshaded/grbl-Mega.git
* Build it with `make` or arduino or whatever...
* Flash it with your preferred ISP programmer
* Change Mega's hFuse to a more common value: `avrdude -p atmega2560 -Uhfuse:w:0xD9:m`
* Configure grbl runtime params (my settings can be used from `system/grbl.cfg`, you can copy-paste them to web-interface later)

## Setting up ArchLinuxArm (ALARM) image
Base instructions taken from https://wiki.archlinux.org/index.php/NanoPi_M1
  
* Create a single primary partition on sdcard and format it to ext4 with `mkfs.ext4 -O '^metadata_csum,^64bit' /dev/sdX1`
* Mount it to some local dir and extract fresh ALARM image from root shell (sudo does not work here!)
```
$ mkdir -p nanopi_arch/mnt
$ cd nanopi_arch/
$ wget http://archlinuxarm.org/os/ArchLinuxARM-armv7-latest.tar.gz
# mount /dev/sdX1 mnt
# bsdtar -xpf ArchLinuxARM-armv7-latest.tar.gz -C mnt/
# sync
```
* Use customized `boot.cmd` from this repo `system` dir to create `boot.scr` for U-Boot. It simply trys to load custom dtb
  file from `/boot/` dir before pacman's managed `/boot/dtbs/`. This dtb is customized to enable extra uarts instead of plain gpios.
```
# mkimage -A arm -O linux -T script -C none -a 0 -e 0 -n "NanoPi M1 Boot Script" -d /.../repo_dir/system/boot.cmd mnt/boot/boot.scr
# # install device tree compiler if not already installed
# pacman -S dtc
# # compile custom dts to a binary form (or simply copy compiled version from `system`)
# dtc -I dts -O dtb -o mnt/sun8i-h3-nanopi-m1.dtb /.../repo_dir/system/sun8i-h3-nanopi-m1.dts
# # you can also compare this custom dts to ALARM's provided version by decompiling it with
# # dtc -I dtb -O dts -o mnt/sun8i-h3-nanopi-m1.dts mnt/sun8i-h3-nanopi-m1.dtb
# unmount mnt
```
* Download/checkout/build/flash latest U-Boot with custom build config. This config disables framebuffer handover from bootloader to
kernel as this feature seems to be broken for nanopi (kernel fails to start with connected hdmi device). By the way, u-boot's `make menuconfig`
feature might be slightly broken as it fails to save your config twice in a row. You must perform a load before second save.
```
$ git clone http://git.denx.de/u-boot.git
$ git tag -l
$ git checkout tags/v2019.04 # or whatever tag is the latest
$ cd u-boot
$ cp /.../repo_dir/system/nanopi_m1_nofb_defconfig configs/
$ make -j4 ARCH=arm CROSS_COMPILE=arm-none-eabi- nanopi_m1_nofb_defconfig
$ make -j4 ARCH=arm CROSS_COMPILE=arm-none-eabi-
# dd if=u-boot-sunxi-with-spl.bin of=/dev/sdX bs=1024 seek=8
```
* Arch is now ready to run on Nanopi. Connect via ethernet, setup ssh, users, hostname, timesync and zoneinfo as you prefer.

## Setting up nanopi environment via ssh
Copy these files and chown them to root where necessary:
```
repo_dir/d7print/ -> /opt/d7print/ # Control web-app
repo_dir/system/d7print.service -> /etc/systemd/system/d7print.service # Systemd unit to run it
repo_dir/system/eth0.network -> /etc/systemd/network/eth0.network # Sets eth0 as a preferred adapter
repo_dir/system/wlan0.network -> /etc/systemd/network/wlan0.network # enables dhcp on wlan0 and sets it as a secondary adapter
repo_dir/system/journald.conf -> /etc/systemd/journald.conf # Limits jurnald logs size

# EDIT before copy:
repo_dir/system/wpa_supplicant-wlan0.conf -> /etc/wpa_supplicant/wpa_supplicant-wlan0.conf # setup wifi connection
```

Install python packages and enable systemd services
```
# pacman -S python-flask python-pillow python-pyserial
# chmod 664 /etc/systemd/system/d7print.service
# mkdir /root/uploads
# chmod 644 /root/uploads
# systemctl enable wpa_supplicant@wlan0
# systemctl enable d7print
# systemctl reboot
```

Web-ui should be up and running at port 80 after reboot
 
 ## Some useful notes
 * d7print/mask.png must be customized for used screen-projector pair
 * Framebuffer format is 32 pbs BGRx
 * Display LS055R1SX04 parameters are 1440x2560 68.04×120.96mm 0.04725 mm per pixel
 * Flask does not support background threads well. So we need some way of shutting down hardware managing thread when web app is unloaded by debugger
 or reloader. `/var/run/d7print.guard` file is used for this purpose. Hw manager thread touches this file on startup and dies whenever someone
 else touces it later.
  


