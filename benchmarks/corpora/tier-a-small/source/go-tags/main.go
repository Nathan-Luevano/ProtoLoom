package main

import (
	"fmt"

	"github.com/golang/protobuf/proto"
)

type Record struct {
	Id      uint64  `protobuf:"varint,1,opt,name=id,proto3" json:"id,omitempty"`
	Label   string  `protobuf:"bytes,2,opt,name=label,proto3" json:"label,omitempty"`
	Samples []int32 `protobuf:"zigzag32,3,rep,packed,name=samples,proto3" json:"samples,omitempty"`
}

func (record *Record) Reset()         { *record = Record{} }
func (record *Record) String() string { return proto.CompactTextString(record) }
func (*Record) ProtoMessage()         {}

func main() {
	encoded, err := proto.Marshal(
		&Record{Id: 7, Label: "evidence", Samples: []int32{-3}},
	)
	if err != nil {
		panic(err)
	}
	fmt.Println(len(encoded))
}
