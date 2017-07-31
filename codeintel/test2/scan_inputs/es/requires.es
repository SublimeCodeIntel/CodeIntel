const First = require('first');
const FirstMember = First.member;
FirstMember();

const SecondObj = { Second: require('second') };
const { default: Second, member: SecondMember, s } = SecondObj.Second;
SecondMember();

const tmp = require;
const req = tmp;
const Third = req('third');
const ThirdMember = Third.member;
ThirdMember();

const Fourth_member = require('fourth').member;
