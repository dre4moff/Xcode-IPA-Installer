import Foundation

@main
enum BundleIDPolicyTests {
    static func main() throws {
        let vectors = [
            ("com.example.largeapp", "ABCDEFGHIJ", "1234567890abcdef1234567890abcdef"),
            ("COM.Example.Long-Name", "ABCDEFGHIJ", String(repeating: "f", count: 32)),
            ("a." + String(repeating: "z", count: 250), "0123456789", String(repeating: "a", count: 32))
        ]
        var results: [[String:String]] = []
        for (original,team,namespace) in vectors {
            guard let value=BundleIDPolicy.make(original:original,team:team,namespace:namespace) else { fatalError("Valid ID rejected") }
            precondition(value==BundleIDPolicy.make(original:original,team:team,namespace:namespace))
            results.append(["original":original,"team":team,"namespace":namespace,"bundleID":value])
        }
        precondition(BundleIDPolicy.make(original:"invalid",team:"ABCDEFGHIJ",namespace:String(repeating:"a",count:32))==nil)
        precondition(BundleIDPolicy.make(original:"valid.app",team:"invalid",namespace:String(repeating:"a",count:32))==nil)
        let data=try JSONSerialization.data(withJSONObject:results,options:[.sortedKeys])
        print(String(data:data,encoding:.utf8)!)
    }
}
