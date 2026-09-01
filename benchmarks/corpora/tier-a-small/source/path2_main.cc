#include "schema.pb.h"

int main() {
  path2::Record record;
  return static_cast<int>(record.ByteSizeLong());
}
