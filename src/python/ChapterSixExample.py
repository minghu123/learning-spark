"""Contains the Chapter 6 Example illustrating accumulators, broadcast variables, numeric operations, and pipe."""
import bisect
import re
import sys
import urllib3
import json
import math

from pyspark import SparkContext
from pyspark import SparkFiles

sparkMaster = sys.argv[1]
inputFile = sys.argv[2]
outputDir = sys.argv[3]

sc = SparkContext(sparkMaster, appName="ChapterSixExample")
file = sc.textFile(inputFile)

# Count lines with KK6JKQ using accumulators
count = sc.accumulator(0)
def incrementCounter(line):
    global count # Access the counter
    if "KK6JKQ" in line:
        count += 1

file.foreach(incrementCounter)
print "Lines with KK6JKQ %d" % count.value


# Create Accumulator[Int] initialized to 0
blankLines = sc.accumulator(0)
dataLines = sc.accumulator(0)

def extractCallSigns(line):
    global blankLines, dataLines # Access the counters
    if (line == ""):
        blankLines += 1
    return line.split(" ")

callSigns = file.flatMap(extractCallSigns)
callSigns.saveAsTextFile(outputDir + "/callsigns")
print "Blank lines %d" % blankLines.value

# Create Accumulators for validating call signs
validSignCount = sc.accumulator(0)
invalidSignCount = sc.accumulator(0)

def validateSign(sign):
    global validSignCount, invalidSignCount
    if re.match(r"\A\d?[a-zA-Z]{1,2}\d{1,4}[a-zA-Z]{1,3}\Z", sign):
        validSignCount += 1
        return True
    else:
        invalidSignCount += 1
        return False

validSigns = callSigns.filter(validateSign)
contactCount = validSigns.map(lambda sign: (sign, 1)).reduceByKey((lambda x, y: x + y))
# Force evaluation so the counters are populated
contactCount.count()
if invalidSignCount.value < 0.1 * validSignCount.value:
    contactCount.saveAsTextFile(outputDir + "/contactCount")
else:
    print "Too many errors %d in %d" % (invalidSignCount.value, validSignCount.value)

# Helper functions for looking up the call signs
def lookupCountry(sign, prefixes):
    pos = bisect.bisect_left(prefixes, sign)
    return prefixes[pos].split(",")[1]

def loadCallSignTable():
    f = open("./files/callsign_tbl_sorted", "r")
    return f.readlines()

# Lookup the locations of the call signs
signPrefixes = sc.broadcast(loadCallSignTable())

def processSignCount(sign_count):
    country = lookupCountry(sign_count[0], signPrefixes.value)
    count = sign_count[1]
    return (country, count)

countryContactCount = (contactCount
                       .map(processSignCount)
                       .reduceByKey((lambda x, y: x+ y)))

countryContactCount.saveAsTextFile(outputDir + "/countries.txt")

# Query 73s for the call signs QSOs and parse the personse

def processCallSigns(signs):
    """Process call signs using a connection pool"""
    http = urllib3.PoolManager()
    urls = map(lambda x: "http://73s.com/qsos/%s.json" % x, signs)
    requests = map(lambda x : (x, http.request('GET', x)), urls)
    result = map(lambda x : (x[0], json.loads(x[1].data)), requests)
    return filter(lambda x: x[1] is not None, result)

def fetchCallSigns(input):
    """Fetch call signs"""
    return input.mapPartitions(lambda callSigns : processCallSigns(callSigns))

contactsContactList = fetchCallSigns(validSigns)

# Compute the distance of each call using an external R program
distScript = "./src/R/finddistance.R"
distScriptName = "finddistance.R"
sc.addFile(distScript)
def hasDistInfo(call):
    """Verify that a call has the fields required to compute the distance"""
    requiredFields = ["mylat", "mylong", "contactlat", "contactlong"]
    return all(map(lambda f: call[f], requiredFields))
def formatCall(call):
    """Format a call so that it can be parsed by our R program"""
    return "{0},{1},{2},{3}".format(
        call["mylat"], call["mylong"],
        call["contactlat"], call["contactlong"])

pipeInputs = contactsContactList.values().flatMap(
    lambda calls: map(formatCall, filter(hasDistInfo, calls)))
distance = pipeInputs.pipe(SparkFiles.get(distScriptName),
                           env={"SEPARATOR" : ","})
distances = distance.collect()
print distances
# Convert our RDD of strings to numeric data so we can compute stats and
# remove the outliers.
distanceNumeric = distance.map(lambda string: float(string))
stats = distanceNumeric.stats()
stddev = math.sqrt(stats.variance())
mean = distanceNumeric.mean()
reasonableDistnace = distanceNumeric.filter(lambda x: math.fabs(x - mean) < 3 * stddev)
print reasonableDistnace.collect()
