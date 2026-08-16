#include <iostream>

#include "path1.pb.h"

int main() {
  std::cout << path1::Record::descriptor()->full_name() << "\n";
  return 0;
}
