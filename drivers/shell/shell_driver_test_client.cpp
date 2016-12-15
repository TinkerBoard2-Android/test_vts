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

#include "shell_driver_test_client.h"

#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <string.h>
#include <math.h>

#include <sstream>
#include <iostream>

#include "shell_driver.h"
#include "shell_msg_protocol.h"
#include "test/vts/proto/VtsDriverControlMessage.pb.h"

using namespace std;

namespace android {
namespace vts {


static int kMaxRetry = 3;

/*
 * send a command to the driver on specified UNIX domain socket and print out
 * the outputs from driver.
 */
char* vts_shell_driver_test_client_start(char* cmd, char* addr_socket) {
  struct sockaddr_un address;
  int socket_fd;
  int nbytes;

  socket_fd = socket(PF_UNIX, SOCK_STREAM, 0);
  if (socket_fd < 0) {
    fprintf(stderr, "socket() failed\n");
    return NULL;
  }

  memset(&address, 0, sizeof(struct sockaddr_un));

  address.sun_family = AF_UNIX;
  strcpy(address.sun_path, addr_socket);

  int conn_success;
  int retry_count = 0;

  conn_success = connect(socket_fd, (struct sockaddr*) &address,
                         sizeof(struct sockaddr_un));
  for (retry_count = 0; retry_count < kMaxRetry && conn_success != 0;
      retry_count++) {  // retry if server not ready
    printf("Client: connection failed, retrying...\n");
    retry_count++;
    if (usleep(50 * pow(retry_count, 3)) != 0) {
      fprintf(stderr, "shell driver unit test: sleep intrupted.");
    }

    conn_success = connect(socket_fd, (struct sockaddr*) &address,
                           sizeof(struct sockaddr_un));
  }

  if (conn_success != 0) {
    fprintf(stderr, "connect() failed\n");
    return NULL;
  }

  VtsDriverControlCommandMessage cmd_msg;

  string cmd_str(cmd);
  cmd_msg.add_shell_command(cmd_str);

  int success;

  success = write_pb_msg(socket_fd, (google::protobuf::Message*) &cmd_msg);
  if (success != 0) {
    fprintf(stderr, "Client: write command failed.\n");
    return NULL;
  }

  // read driver output
  VtsDriverControlResponseMessage out_msg;

  success = read_pb_msg(socket_fd, (google::protobuf::Message*) &out_msg);
  if (success != 0) {
    fprintf(stderr, "Client: write command failed.\n");
    return NULL;
  }

  printf("Client receiving output:\n");

  // TODO(yuexima) use vector for output messages
  stringstream ss;
  for (int i = 0; i < out_msg.stdout_size(); i++) {
    string out_str = out_msg.stdout(i);
    cout << "[Shell driver] output for command " << i
        << ": " << out_str << endl;
    ss << out_str;
  }

  close(socket_fd);

  string res_str = ss.str();
  char* res = (char*) malloc(res_str.length() + 1);
  strcpy(res, res_str.c_str());

  return res;
}

}  // namespace vts
}  // namespace android
