/*
 * Copyright 2016 The Android Open Source Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "BinderServer.h"

#include <stdio.h>
#include <stdlib.h>

#include <string>
#include <iostream>

#include <utils/RefBase.h>
#define LOG_TAG "VtsFuzzerBinderServer"
#include <utils/Log.h>
#include <utils/String8.h>

#include <binder/TextOutput.h>
#include <binder/IInterface.h>
#include <binder/IBinder.h>
#include <binder/ProcessState.h>
#include <binder/IServiceManager.h>
#include <binder/IPCThreadState.h>

#include "binder/VtsFuzzerBinderService.h"
#include "specification_parser/SpecificationBuilder.h"

#include <google/protobuf/text_format.h>
#include "test/vts/sysfuzzer/common/proto/InterfaceSpecificationMessage.pb.h"


using namespace std;

namespace android {
namespace vts {


class BnVtsFuzzer : public BnInterface<IVtsFuzzer> {
  virtual status_t onTransact(
      uint32_t code, const Parcel& data, Parcel* reply, uint32_t flags = 0);
};


status_t BnVtsFuzzer::onTransact(
    uint32_t code, const Parcel& data, Parcel* reply, uint32_t flags) {
  ALOGD("BnVtsFuzzer::%s(%i) %i", __FUNCTION__, code, flags);

  data.checkInterface(this);
#ifdef VTS_FUZZER_BINDER_DEBUG
  data.print(PLOG);
  endl(PLOG);
#endif

  switch(code) {
    case EXIT:
      Exit();
      break;
    case LOAD_HAL: {
      const char* path = data.readCString();
      const int target_class = data.readInt32();
      const int target_type = data.readInt32();
      const float target_version = data.readFloat();
      const char* module_name = data.readCString();
      int32_t result = LoadHal(string(path), target_class, target_type,
                               target_version, string(module_name));
      ALOGD("BnVtsFuzzer::%s LoadHal(%s) -> %i",
            __FUNCTION__, path, result);
      if (reply == NULL) {
        ALOGE("reply == NULL");
        abort();
      }
#ifdef VTS_FUZZER_BINDER_DEBUG
      reply->print(PLOG);
      endl(PLOG);
#endif
      reply->writeInt32(result);
      break;
    }
    case STATUS: {
      int32_t type = data.readInt32();
      int32_t result = Status(type);

      ALOGD("BnVtsFuzzer::%s status(%i) -> %i",
            __FUNCTION__, type, result);
      if (reply == NULL) {
        ALOGE("reply == NULL");
        abort();
      }
#ifdef VTS_FUZZER_BINDER_DEBUG
      reply->print(PLOG);
      endl(PLOG);
#endif
      reply->writeInt32(result);
      break;
    }
    case CALL: {
      const char* arg = data.readCString();
      const char* result = Call(arg);

      ALOGD("BnVtsFuzzer::%s call(%s) = %i",
            __FUNCTION__, arg, result);
      if (reply == NULL) {
        ALOGE("reply == NULL");
        abort();
      }
#ifdef VTS_FUZZER_BINDER_DEBUG
      reply->print(PLOG);
      endl(PLOG);
#endif
      reply->writeCString(result);
      break;
    }
    case GET_FUNCTIONS: {
      const char* result = GetFunctions();

      if (reply == NULL) {
        ALOGE("reply == NULL");
        abort();
      }
#ifdef VTS_FUZZER_BINDER_DEBUG
      reply->print(PLOG);
      endl(PLOG);
#endif
      reply->writeCString(result);
      break;
    }
    default:
      return BBinder::onTransact(code, data, reply, flags);
  }
  return NO_ERROR;
}


class VtsFuzzerServer : public BnVtsFuzzer {

 public:
  VtsFuzzerServer(android::vts::SpecificationBuilder& spec_builder,
                  const char* lib_path)
      : spec_builder_(spec_builder),
        lib_path_(lib_path) {}

  void Exit() {
    printf("VtsFuzzerServer::Exit\n");
  }

  int32_t LoadHal(const string& path, int target_class,
                  int target_type, float target_version,
                  const string& module_name) {
    printf("VtsFuzzerServer::LoadHal(%s)\n", path.c_str());
    bool success = spec_builder_.LoadTargetComponent(
        path.c_str(), lib_path_, target_class, target_type, target_version,
        module_name.c_str());
    cout << "Result: " << success << std::endl;
    if (success) {
      return 0;
    } else {
      return -1;
    }
  }

  int32_t Status(int32_t type) {
    printf("VtsFuzzerServer::Status(%i)\n", type);
    return 0;
  }

  const char* Call(const string& arg) {
    printf("VtsFuzzerServer::Call(%s)\n", arg.c_str());
    FunctionSpecificationMessage* func_msg = new FunctionSpecificationMessage();
    google::protobuf::TextFormat::MergeFromString(arg, func_msg);
    printf("call!!!\n");
    const string& result = spec_builder_.CallFunction(func_msg);
    printf("call done!!!\n");
    return result.c_str();
  }

  const char* GetFunctions() {
    printf("Get functions*");
    vts::InterfaceSpecificationMessage* spec =
        spec_builder_.GetInterfaceSpecification();
    if (!spec) {
      return NULL;
    }
    string* output = new string();
    printf("getfunctions serial1\n");
    if (google::protobuf::TextFormat::PrintToString(*spec, output)) {
      printf("getfunctions length %d\n", output->length());
      return output->c_str();
    } else {
      printf("can't serialize the interface spec message to a string.\n");
      return NULL;
    }
  }

 private:
  android::vts::SpecificationBuilder& spec_builder_;
  const char* lib_path_;
};


void StartBinderServer(android::vts::SpecificationBuilder& spec_builder,
                       const char* lib_path) {
  defaultServiceManager()->addService(
      String16(VTS_FUZZER_BINDER_SERVICE_NAME),
      new VtsFuzzerServer(spec_builder, lib_path));
  android::ProcessState::self()->startThreadPool();
  IPCThreadState::self()->joinThreadPool();
}

}  // namespace vts
}  // namespace android
