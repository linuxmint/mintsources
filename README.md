#Mintsources

This is a software configuration tool, mintsources, allows the user to adjust sofware repositories, select a mirror, include PPAs and do package management tasks.

![image](https://user-images.githubusercontent.com/2152854/126929235-5cd892c7-e951-4a5d-9067-770329f04427.png)


## Build
Get source code
```
git clone https://github.com/linuxmint/mintsources
cd mintsources
```
Build
```
dpkg-buildpackage --no-sign
```
Install
```
cd ..
sudo dpkg -i mintsources*.deb
```

## Translations
Please use Launchpad to translate Mintsources: https://translations.launchpad.net/linuxmint/latest/.

## License
- Code: GPLv3
