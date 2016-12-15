#!/bin/bash

PYTHONPATH=$PYTHONPATH:.. python -m vts.testcases.host.light.conventional.SampleLightTest $ANDROID_BUILD_TOP/test/vts/testcases/host/light/conventional/SampleLightTest.config
# PYTHONPATH=$PYTHONPATH:.. python -m vts.testcases.host.bluetooth.conventional.SampleBluetoothTest $ANDROID_BUILD_TOP/test/vts/testcases/host/bluetooth/conventional/SampleBluetoothTest.config
# PYTHONPATH=$PYTHONPATH:.. python -m vts.testcases.fuzz.hal_light.conventional.LightFuzzTest $ANDROID_BUILD_TOP/test/vts/testcases/fuzz/hal_light/conventional/LightFuzzTest.config
# PYTHONPATH=$PYTHONPATH:.. python -m vts.testcases.fuzz.hal_light.conventional_standalone.StandaloneLightFuzzTest $ANDROID_BUILD_TOP/test/vts/testcases/fuzz/hal_light/conventional_standalone/StandaloneLightFuzzTest.config
# PYTHONPATH=$PYTHONPATH:.. python -m vts.testcases.host.camera.conventional.SampleCameraTest $ANDROID_BUILD_TOP/test/vts/testcases/host/camera/conventional/SampleCameraTest.config
# PYTHONPATH=$PYTHONPATH:.. python -m vts.testcases.host.nfc.hidl.NfcHidlBasicTest $ANDROID_BUILD_TOP/test/vts/testcases/host/nfc/hidl/NfcHidlBasicTest.config
# PYTHONPATH=$PYTHONPATH:.. python -m vts.testcases.host.shell.SampleShellTest $ANDROID_BUILD_TOP/test/vts/testcases/host/shell/SampleShellTest.config
# PYTHONPATH=$PYTHONPATH:.. python -m vts.testcases.fuzz_test.lib_bionic.LibBionicLibmFuzzTest $ANDROID_BUILD_TOP/test/vts/testcases/fuzz_test/lib_bionic/LibBionicLibmFuzzTest.config
