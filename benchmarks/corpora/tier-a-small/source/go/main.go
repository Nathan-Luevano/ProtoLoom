package main

import (
	"fmt"

	"example.com/protoloomfixture/schema"
)

func main() {
	fmt.Println((&schema.Record{}).ProtoReflect().Descriptor().FullName())
}
