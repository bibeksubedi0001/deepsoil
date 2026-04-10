###############################################################################
#                                                                             #
#  1D Non-Linear Site Response Analysis — OpenSees TCL Script                 #
#                                                                             #
#  Methodology:                                                               #
#    - Total-stress analysis using quad elements (plane strain)               #
#    - Lysmer-Kuhlmeyer compliant base (viscous dashpot)                      #
#    - Periodic (tied) lateral boundary conditions                            #
#    - Rayleigh damping: 2% at f1 and 5*f1                                   #
#    - Newmark integration (gamma=0.5, beta=0.25)                             #
#    - Newton algorithm with Krylov-Newton fallback                           #
#                                                                             #
#  Material Models:                                                           #
#    Sand/Gravel -> PressureDependMultiYield (PDMY)                           #
#    Silt        -> PressureIndependMultiYield (PIMY, elastic+hysteretic)     #
#    Clay        -> PressureIndependMultiYield (PIMY, undrained)              #
#                                                                             #
#  Usage:                                                                     #
#    opensees SiteResponse1D.tcl <soil_csv> <motion_acc> <dt> <npts> <outdir> #
#                                                                             #
#  Reference: Adhikari (2023), OpenSees UC Davis examples, Lysmer &           #
#             Kuhlmeyer (1969), Joyner & Chen (1975)                          #
#                                                                             #
###############################################################################

wipe

# =============================================================================
# 0. PARSE COMMAND-LINE ARGUMENTS
# =============================================================================

if {$argc < 5} {
    puts "Usage: opensees SiteResponse1D.tcl <soil_csv> <motion_acc> <dt> <npts> <outdir>"
    puts "  soil_csv   : path to soil profile CSV file"
    puts "  motion_acc : path to single-column acceleration file (g)"
    puts "  dt         : time step of input motion (s)"
    puts "  npts       : number of points in motion"
    puts "  outdir     : output directory for recorders"
    exit
}

set soilFile   [lindex $argv 0]
set motionFile [lindex $argv 1]
set motionDT   [lindex $argv 2]
set motionNPTS [lindex $argv 3]
set outputDir  [lindex $argv 4]

# Create output directory
file mkdir $outputDir

puts "============================================================"
puts "1D Non-Linear Site Response Analysis"
puts "============================================================"
puts "Soil profile : $soilFile"
puts "Motion       : $motionFile"
puts "dt=$motionDT s, npts=$motionNPTS"
puts "Output       : $outputDir"
puts "============================================================"

# =============================================================================
# 1. CONSTANTS & PARAMETERS
# =============================================================================

set g           9.81          ;# gravitational acceleration (m/s^2)
set colWidth    1.0           ;# column width (m) — unit width for 1D
set rockVs      760.0         ;# bedrock Vs (m/s) — NEHRP B/C boundary
set rockDen     2200.0        ;# bedrock density (kg/m^3)
set dampRatio   0.02          ;# target Rayleigh damping ratio (2%)
set nu          0.3           ;# Poisson's ratio (assumed)

# =============================================================================
# 2. READ AND PARSE SOIL PROFILE CSV
# =============================================================================

puts "\nReading soil profile..."

# TCL CSV parser — handles quoted fields with commas
proc parseCsvLine {line} {
    set fields {}
    set current ""
    set inQuote 0
    foreach char [split $line ""] {
        if {$char eq "\"" } {
            set inQuote [expr {!$inQuote}]
        } elseif {$char eq "," && !$inQuote} {
            lappend fields [string trim $current]
            set current ""
        } else {
            append current $char
        }
    }
    lappend fields [string trim $current]
    return $fields
}

# Read CSV file
set fp [open $soilFile r]
set headerLine [gets $fp]
# Parse header to find column indices
set headers [parseCsvLine $headerLine]
set colIdx_thick     [lsearch $headers "thickness"]
set colIdx_density   [lsearch $headers "mass_density"]
set colIdx_Vs        [lsearch $headers "Vs"]
set colIdx_soilType  [lsearch $headers "soil_type"]
set colIdx_pdmy      [lsearch $headers "pdmy_args"]
set colIdx_hyst      [lsearch $headers "hyst_args"]
set colIdx_clay      [lsearch $headers "clay_args"]

# Storage lists
set layerThick    {}
set layerDensity  {}
set layerVs       {}
set layerType     {}
set layerPdmy     {}
set layerHyst     {}
set layerClay     {}

set numLayers 0
while {[gets $fp line] >= 0} {
    set line [string trim $line]
    if {$line eq ""} continue

    set fields [parseCsvLine $line]
    set thick  [lindex $fields $colIdx_thick]
    set dens   [lindex $fields $colIdx_density]
    set vs     [lindex $fields $colIdx_Vs]
    set stype  [lindex $fields $colIdx_soilType]
    set pdmy   [lindex $fields $colIdx_pdmy]
    set hyst   [lindex $fields $colIdx_hyst]
    set clay   [lindex $fields $colIdx_clay]

    # Skip layers with zero/missing thickness
    if {$thick <= 0.001} continue

    lappend layerThick   $thick
    lappend layerDensity $dens
    lappend layerVs      $vs
    lappend layerType    $stype
    lappend layerPdmy    $pdmy
    lappend layerHyst    $hyst
    lappend layerClay    $clay
    incr numLayers
}
close $fp

# Compute total depth and Vs-average
set totalDepth 0.0
set sumH 0.0
set sumH_Vs 0.0
for {set i 0} {$i < $numLayers} {incr i} {
    set h  [lindex $layerThick $i]
    set vs [lindex $layerVs $i]
    set totalDepth [expr {$totalDepth + $h}]
    set sumH       [expr {$sumH + $h}]
    set sumH_Vs    [expr {$sumH_Vs + $h / $vs}]
}
set VsAvg [expr {$sumH / $sumH_Vs}]

puts "  Layers     : $numLayers"
puts "  Total depth: $totalDepth m"
puts "  Vs30 (avg) : [format %.1f $VsAvg] m/s"

# =============================================================================
# 3. COMPUTE ELEMENT DISCRETIZATION
# =============================================================================
# Rule: max element height <= Vs_min / (10 * f_max)
# f_max = 25 Hz  =>  h_max = Vs / 250

set fMax 25.0

# For each soil layer, determine the number of sub-elements
set numElems      {}       ;# sub-elements per layer
set elemThick     {}       ;# thickness of each element (flat list)
set elemDensity   {}       ;# density of each element
set elemVs        {}       ;# Vs of each element
set elemType      {}       ;# soil type of each element
set elemPdmy      {}       ;# pdmy args string
set elemHyst      {}       ;# hyst args string
set elemClay      {}       ;# clay args string
set elemMatTag    {}       ;# material tag for each element

set totalElems 0
for {set i 0} {$i < $numLayers} {incr i} {
    set h    [lindex $layerThick $i]
    set vs   [lindex $layerVs $i]
    set hmax [expr {$vs / (10.0 * $fMax)}]
    # Ensure at least 1 element, enforce practical minimum and maximum
    if {$hmax > 1.5} {set hmax 1.5}
    if {$hmax < 0.25} {set hmax 0.25}
    set nSub [expr {int(ceil($h / $hmax))}]
    if {$nSub < 1} {set nSub 1}
    set subH [expr {$h / double($nSub)}]

    lappend numElems $nSub

    for {set j 0} {$j < $nSub} {incr j} {
        lappend elemThick   $subH
        lappend elemDensity [lindex $layerDensity $i]
        lappend elemVs      [lindex $layerVs $i]
        lappend elemType    [lindex $layerType $i]
        lappend elemPdmy    [lindex $layerPdmy $i]
        lappend elemHyst    [lindex $layerHyst $i]
        lappend elemClay    [lindex $layerClay $i]
        lappend elemMatTag  [expr {$i + 1}]  ;# same material for all sub-elems
        incr totalElems
    }
}

puts "  Elements   : $totalElems (after sub-discretization)"

# =============================================================================
# 4. BUILD MODEL
# =============================================================================
# 2D model with 2 DOFs per node (horizontal disp, vertical disp)
# Quad elements in plane strain

model BasicBuilder -ndm 2 -ndf 2

puts "\nBuilding mesh..."

# --- 4a. Create Nodes ---
# Column has 2 vertical lines of nodes (left: odd, right: even)
# Bottom nodes at z=0, surface nodes at z=totalDepth
# Node numbering: node (2*j+1) = left, node (2*j+2) = right
# j = 0 is bottom, j = totalElems is surface

set numNodePairs [expr {$totalElems + 1}]
set zCoord 0.0

for {set j 0} {$j <= $totalElems} {incr j} {
    set nodeL [expr {2 * $j + 1}]
    set nodeR [expr {2 * $j + 2}]
    node $nodeL  0.0        $zCoord
    node $nodeR  $colWidth  $zCoord

    if {$j < $totalElems} {
        set zCoord [expr {$zCoord + [lindex $elemThick $j]}]
    }
}

# Surface node IDs
set surfNodeL [expr {2 * $totalElems + 1}]
set surfNodeR [expr {2 * $totalElems + 2}]
# Base node IDs
set baseNodeL 1
set baseNodeR 2

puts "  Nodes: [expr {2 * ($totalElems + 1)}]  (surface: $surfNodeL/$surfNodeR, base: $baseNodeL/$baseNodeR)"

# --- 4b. Boundary Conditions ---

# Fix base nodes vertically (DOF 2); horizontal will be driven by dashpot
fix $baseNodeL  0 1
fix $baseNodeR  0 1

# Add a dashpot node below the base (fixed reference point)
set dashNodeTag [expr {2 * ($totalElems + 1) + 1}]
node $dashNodeTag 0.0 0.0
fix $dashNodeTag  1 1

# Tie left-right nodes at each level (periodic boundaries for 1D shear)
for {set j 0} {$j <= $totalElems} {incr j} {
    set nodeL [expr {2 * $j + 1}]
    set nodeR [expr {2 * $j + 2}]
    equalDOF $nodeL $nodeR 1 2
}

puts "  Periodic boundary: tied L-R nodes at all [expr {$totalElems + 1}] levels"

# --- 4c. Define Materials ---
puts "\nDefining materials..."

# Track unique material tags (one per original layer)
set definedMats {}

for {set i 0} {$i < $numLayers} {incr i} {
    set matTag [expr {$i + 1}]
    set stype  [lindex $layerType $i]
    set vs     [lindex $layerVs $i]
    set dens   [lindex $layerDensity $i]

    if {$stype eq "sand"} {
        # PressureDependMultiYield
        # pdmy_args: rho, G0, K_bulk, phi, peak_strain, p_ref, press_coeff, pt_ang
        set args [split [lindex $layerPdmy $i] ","]
        set rho       [string trim [lindex $args 0]]
        set G0        [string trim [lindex $args 1]]
        set K_bulk    [string trim [lindex $args 2]]
        set phi       [string trim [lindex $args 3]]
        set peakStr   [string trim [lindex $args 4]]
        set pRef      [string trim [lindex $args 5]]
        set pressCoef [string trim [lindex $args 6]]
        set ptAng     [string trim [lindex $args 7]]

        # nDMaterial PressureDependMultiYield $tag $nd $rho $refShearModul
        #   $refBulkModul $frictionAng $peakShearStra $refPress
        #   $pressDependCoe $PTAng $contrac $dilat1 $dilat2
        #   $liq1 $liq2 $liq4 $noYieldSurf
        # Using default contraction/dilation with 20 yield surfaces
        nDMaterial PressureDependMultiYield $matTag 2 $rho $G0 $K_bulk \
            $phi $peakStr $pRef $pressCoef $ptAng \
            0.21 0.0 0.0 \
            0.0 0.0 0.0 \
            20

    } elseif {$stype eq "silt"} {
        # PressureIndependMultiYield for silt
        # hyst_args: K0, Fy, Kp, Kn — we map these to PIMY parameters
        set args [split [lindex $layerHyst $i] ","]
        set K0 [string trim [lindex $args 0]]
        set Fy [string trim [lindex $args 1]]
        # K0 = G0 = rho * Vs^2, Fy ~ cohesion * sqrt(3)
        set G0     $K0
        set K_bulk [expr {2.0 * $G0 * (1.0 + $nu) / (3.0 * (1.0 - 2.0 * $nu))}]
        # Cohesion estimate from Fy: c = Fy / sqrt(3) ~ Fy / 1.732
        set cohesion [expr {$Fy / 1.732}]
        # Peak shear strain
        set peakStr 0.1

        nDMaterial PressureIndependMultiYield $matTag 2 $dens $G0 $K_bulk \
            $cohesion $peakStr \
            0.0 101325.0 0.0 20

    } elseif {$stype eq "clay"} {
        # PressureIndependMultiYield for clay
        # clay_args: p_ref, e0, lambda_c, kappa, xi
        set args [split [lindex $layerClay $i] ","]
        set pRefClay   [string trim [lindex $args 0]]
        set e0         [string trim [lindex $args 1]]
        set lambda_c   [string trim [lindex $args 2]]
        set kappa      [string trim [lindex $args 3]]
        set xiOCR      [string trim [lindex $args 4]]

        # Derive PIMY parameters from clay properties
        set G0     [expr {$dens * $vs * $vs}]
        set K_bulk [expr {2.0 * $G0 * (1.0 + $nu) / (3.0 * (1.0 - 2.0 * $nu))}]
        # Undrained shear strength estimate: Su ≈ 0.25 * sigma'v (for NC clay)
        # sigma'v ≈ p_ref * 3 / (1 + 2*K0) where K0~0.577
        set sigmaV [expr {$pRefClay * 3.0 / (1.0 + 2.0 * 0.577)}]
        set Su     [expr {0.25 * $sigmaV * $xiOCR}]
        # Minimum cohesion: keep G0/Su ratio below ~500 for numerical stability
        set suMin [expr {$G0 / 500.0}]
        if {$suMin < 10000.0} {set suMin 10000.0}  ;# absolute floor 10 kPa
        if {$Su < $suMin} {set Su $suMin}
        set cohesion $Su
        set peakStr  0.1

        nDMaterial PressureIndependMultiYield $matTag 2 $dens $G0 $K_bulk \
            $cohesion $peakStr \
            0.0 101325.0 0.0 20
    }

    puts "  Mat $matTag: $stype (Vs=[format %.0f $vs] m/s, rho=[format %.0f $dens] kg/m3)"
}

# --- 4d. Create Elements ---
puts "\nCreating elements..."

for {set j 0} {$j < $totalElems} {incr j} {
    set eleTag [expr {$j + 1}]
    # Bottom-left, bottom-right, top-right, top-left (counter-clockwise)
    set n1 [expr {2 * $j + 1}]      ;# bottom-left
    set n2 [expr {2 * $j + 2}]      ;# bottom-right
    set n3 [expr {2 * ($j + 1) + 2}] ;# top-right
    set n4 [expr {2 * ($j + 1) + 1}] ;# top-left
    set matT [lindex $elemMatTag $j]
    set eThick $colWidth
    set dens [lindex $elemDensity $j]

    # quad element: $tag $n1 $n2 $n3 $n4 $thick "PlaneStrain" $matTag
    #   $pressure $density $b1 $b2
    # b1,b2 are body forces per unit volume (N/m³), NOT accelerations
    element quad $eleTag $n1 $n2 $n3 $n4 \
        $eThick "PlaneStrain" $matT \
        0.0 $dens 0.0 [expr {-$dens * $g}]
}

puts "  Created $totalElems quad elements"

# --- 4e. Lysmer-Kuhlmeyer Dashpot at Base ---
# Dashpot coefficient: C = rho_rock * Vs_rock * A_base
# A_base = colWidth * 1.0 (unit out-of-plane thickness)

set dashCoeff [expr {$rockDen * $rockVs * $colWidth}]
puts "\n  Dashpot: C = [format %.1f $dashCoeff] N·s/m (rho_rock=$rockDen, Vs_rock=$rockVs)"

# Viscous uniaxial material for dashpot
set dashMatTag [expr {$numLayers + 1}]
uniaxialMaterial Viscous $dashMatTag $dashCoeff 1.0

# Zero-length element connecting base node to fixed dashpot reference
set dashEleTag [expr {$totalElems + 1}]
element zeroLength $dashEleTag $dashNodeTag $baseNodeL \
    -mat $dashMatTag -dir 1

puts "  Dashpot element $dashEleTag connecting node $dashNodeTag -> $baseNodeL"

# =============================================================================
# 5. RECORDERS
# =============================================================================
puts "\nSetting up recorders..."

# --- Acceleration at all node levels (left column) ---
set nodeList {}
for {set j 0} {$j <= $totalElems} {incr j} {
    lappend nodeList [expr {2 * $j + 1}]
}

# Surface acceleration
recorder Node -file "$outputDir/acc_surface.out" \
    -time -node $surfNodeL -dof 1 accel

# Base acceleration
recorder Node -file "$outputDir/acc_base.out" \
    -time -node $baseNodeL -dof 1 accel

# Acceleration at all levels (for amplification profiles)
recorder Node -file "$outputDir/acc_all_nodes.out" \
    -time -node {*}$nodeList -dof 1 accel

# Surface displacement
recorder Node -file "$outputDir/disp_surface.out" \
    -time -node $surfNodeL -dof 1 disp

# Displacement at all levels
recorder Node -file "$outputDir/disp_all_nodes.out" \
    -time -node {*}$nodeList -dof 1 disp

# Element stress and strain (for hysteresis loops)
# stress: sigma_xx, sigma_yy, sigma_xy, (tau_xz if 3D)
# strain: eps_xx, eps_yy, gamma_xy

set eleList {}
for {set j 0} {$j < $totalElems} {incr j} {
    lappend eleList [expr {$j + 1}]
}

recorder Element -file "$outputDir/stress.out" \
    -time -ele {*}$eleList material 1 stress

recorder Element -file "$outputDir/strain.out" \
    -time -ele {*}$eleList material 1 strain

puts "  Recorders configured in $outputDir"

# =============================================================================
# 6. GRAVITY ANALYSIS (Stage 0 — Elastic)
# =============================================================================
puts "\n--- Gravity Analysis (Elastic Stage) ---"

# Set all materials to elastic (stage 0)
for {set i 0} {$i < $numLayers} {incr i} {
    set matTag [expr {$i + 1}]
    set stype [lindex $layerType $i]
    if {$stype eq "sand"} {
        updateMaterialStage -material $matTag -stage 0
    } else {
        updateMaterialStage -material $matTag -stage 0
    }
}

constraints Transformation
test NormDispIncr 1.0e-5 40 0
algorithm Newton
numberer RCM
system ProfileSPD
integrator Newmark 0.5 0.25
analysis Transient

# Apply gravity in 10 steps
set gravOK [analyze 10 5.0e2]

if {$gravOK != 0} {
    puts "  WARNING: Gravity elastic analysis did not converge. Trying more steps..."
    algorithm KrylovNewton
    set gravOK [analyze 50 5.0e2]
}

puts "  Elastic gravity: [expr {$gravOK == 0 ? {OK} : {FAILED}}]"

# =============================================================================
# 7. SWITCH TO PLASTIC (Stage 1) + Reconverge
# =============================================================================
puts "\n--- Updating Material Stage to Plastic ---"

for {set i 0} {$i < $numLayers} {incr i} {
    set matTag [expr {$i + 1}]
    updateMaterialStage -material $matTag -stage 1
}

# Re-converge with plastic materials
algorithm Newton
set plasticOK [analyze 10 5.0e2]

if {$plasticOK != 0} {
    puts "  WARNING: Plastic re-convergence issue. Trying KrylovNewton..."
    algorithm KrylovNewton
    set plasticOK [analyze 50 5.0e2]
}

puts "  Plastic gravity: [expr {$plasticOK == 0 ? {OK} : {FAILED}}]"

# --- Reset time and displacements ---
setTime 0.0
wipeAnalysis
remove recorders

# Reset displacements to zero (keep stresses)
for {set j 0} {$j <= $totalElems} {incr j} {
    set nL [expr {2 * $j + 1}]
    set nR [expr {2 * $j + 2}]
    setNodeDisp $nL 1 0.0
    setNodeDisp $nL 2 0.0
    setNodeDisp $nR 1 0.0
    setNodeDisp $nR 2 0.0
}
setNodeDisp $dashNodeTag 1 0.0
setNodeDisp $dashNodeTag 2 0.0

puts "  Time and displacements reset to zero.\n"

# =============================================================================
# 8. RAYLEIGH DAMPING
# =============================================================================

# Fundamental frequency of soil column
set f1 [expr {$VsAvg / (4.0 * $totalDepth)}]
set f2 [expr {5.0 * $f1}]
set omega1 [expr {2.0 * 3.14159265 * $f1}]
set omega2 [expr {2.0 * 3.14159265 * $f2}]

set a0 [expr {2.0 * $dampRatio * $omega1 * $omega2 / ($omega1 + $omega2)}]
set a1 [expr {2.0 * $dampRatio / ($omega1 + $omega2)}]

puts "--- Rayleigh Damping ---"
puts "  f1 = [format %.2f $f1] Hz,  f2 = [format %.2f $f2] Hz"
puts "  xi = $dampRatio"
puts "  a0 = [format %.6f $a0],  a1 = [format %.6f $a1]"

rayleigh $a0 $a1 0.0 0.0

# =============================================================================
# 9. DYNAMIC ANALYSIS — APPLY EARTHQUAKE
# =============================================================================
puts "\n--- Dynamic Analysis ---"

# Re-setup recorders after wipeAnalysis
recorder Node -file "$outputDir/acc_surface.out" \
    -time -node $surfNodeL -dof 1 accel

recorder Node -file "$outputDir/acc_base.out" \
    -time -node $baseNodeL -dof 1 accel

recorder Node -file "$outputDir/acc_all_nodes.out" \
    -time -node {*}$nodeList -dof 1 accel

recorder Node -file "$outputDir/disp_surface.out" \
    -time -node $surfNodeL -dof 1 disp

recorder Node -file "$outputDir/disp_all_nodes.out" \
    -time -node {*}$nodeList -dof 1 disp

recorder Element -file "$outputDir/stress.out" \
    -time -ele {*}$eleList material 1 stress

recorder Element -file "$outputDir/strain.out" \
    -time -ele {*}$eleList material 1 strain

# --- Input Motion ---
# Acceleration in g → convert to m/s^2
set accelFactor [expr {$g}]  ;# multiply g-values by 9.81 to get m/s²

# For compliant base: apply force through dashpot
# Force = C * velocity, where velocity = integral(accel)
# OpenSees applies input as: -accel through UniformExcitation at the base

# Method: UniformExcitation applies uniform acceleration to all nodes
# With compliant base dashpot, the motion is effectively deconvolved

timeSeries Path 1 -dt $motionDT -filePath $motionFile -factor $accelFactor

pattern UniformExcitation 1 1 -accel 1

puts "  Motion loaded: $motionFile"
puts "  Factor: $accelFactor (g -> m/s2)"
puts "  Duration: [format %.1f [expr {$motionNPTS * $motionDT}]] s"
puts "  dt = $motionDT s"

# --- Analysis setup ---
constraints Transformation
test NormDispIncr 1.0e-3 50 0
algorithm KrylovNewton
numberer RCM
system ProfileSPD
integrator Newmark 0.5 0.25
analysis Transient

# --- Run dynamic analysis ---
# Cap analysis dt at 0.005s for nonlinear stability.
# OpenSees Path timeSeries interpolates the input motion automatically.
set maxAnalysisDT 0.005
if {$motionDT > $maxAnalysisDT} {
    set analysisdt $maxAnalysisDT
    puts "  Analysis dt capped: $motionDT -> $analysisdt s (input motion interpolated)"
} else {
    set analysisdt $motionDT
}
set totalTime  [expr {$motionNPTS * $motionDT}]
set currentTime 0.0
set ok 0
set stepCount 0
set failCount 0

puts "\n  Running dynamic analysis..."
puts "  Total time: [format %.1f $totalTime] s"

while {$currentTime < $totalTime && $ok == 0} {
    set ok [analyze 1 $analysisdt]

    if {$ok != 0} {
        # Multi-level convergence recovery
        # Level 1: dt/2, KrylovNewton
        set ok [analyze 1 [expr {$analysisdt / 2.0}]]
        if {$ok != 0} {
            # Level 2: dt/4, KrylovNewton
            set ok [analyze 2 [expr {$analysisdt / 4.0}]]
        }
        if {$ok != 0} {
            # Level 3: dt/4, ModifiedNewton
            algorithm ModifiedNewton
            set ok [analyze 2 [expr {$analysisdt / 4.0}]]
            algorithm KrylovNewton
        }
        if {$ok != 0} {
            # Level 4: dt/8, NewtonLineSearch
            algorithm NewtonLineSearch 0.8
            set ok [analyze 4 [expr {$analysisdt / 8.0}]]
            algorithm KrylovNewton
        }
        if {$ok != 0} {
            # Level 5: dt/16, BFGS, relaxed tolerance
            test NormDispIncr 5.0e-3 60 0
            algorithm BFGS
            set ok [analyze 8 [expr {$analysisdt / 16.0}]]
            test NormDispIncr 1.0e-3 50 0
            algorithm KrylovNewton
        }
        if {$ok != 0} {
            # Level 6: dt/32, BFGS, further relaxed
            test NormDispIncr 1.0e-2 80 0
            algorithm BFGS
            set ok [analyze 16 [expr {$analysisdt / 32.0}]]
            test NormDispIncr 1.0e-3 50 0
            algorithm KrylovNewton
        }
        if {$ok != 0} {
            incr failCount
            puts "    Failure #$failCount at t=[format %.3f $currentTime]s — skipping timestep"
            if {$failCount > 50} {
                puts "    FATAL: Too many failures ($failCount). Aborting at t=[format %.3f $currentTime]s"
                break
            }
            # Force time forward as last resort
            test NormDispIncr 1.0e-1 10 0
            algorithm KrylovNewton
            analyze 1 $analysisdt
            test NormDispIncr 1.0e-3 50 0
            algorithm KrylovNewton
            set ok 0
        }
    }

    set currentTime [getTime]
    incr stepCount

    # Progress indicator every 10% of duration
    if {[expr {$stepCount % int($motionNPTS / 10 + 1)}] == 0} {
        puts "    t = [format %.1f $currentTime] s  ([format %.0f [expr {100.0 * $currentTime / $totalTime}]]%)"
    }
}

puts "\n  Analysis complete."
puts "  Final time: [format %.3f [getTime]] s"
puts "  Steps: $stepCount, Failures: $failCount"

# =============================================================================
# 10. WRITE PROFILE METADATA
# =============================================================================

# Write a metadata file so post-processing knows the model structure
set metaFp [open "$outputDir/model_info.txt" w]
puts $metaFp "# 1D Site Response Analysis - Model Information"
puts $metaFp "soil_profile $soilFile"
puts $metaFp "motion_file $motionFile"
puts $metaFp "motion_dt $motionDT"
puts $metaFp "motion_npts $motionNPTS"
puts $metaFp "num_layers $numLayers"
puts $metaFp "num_elements $totalElems"
puts $metaFp "total_depth $totalDepth"
puts $metaFp "Vs_avg [format %.2f $VsAvg]"
puts $metaFp "damping_ratio $dampRatio"
puts $metaFp "f1 [format %.4f $f1]"
puts $metaFp "f2 [format %.4f $f2]"
puts $metaFp "col_width $colWidth"
puts $metaFp "rock_Vs $rockVs"
puts $metaFp "rock_density $rockDen"
puts $metaFp "surface_nodeL $surfNodeL"
puts $metaFp "base_nodeL $baseNodeL"
puts $metaFp "#"
puts $metaFp "# Node level elevations (left-column nodes):"
set zz 0.0
for {set j 0} {$j <= $totalElems} {incr j} {
    set nL [expr {2 * $j + 1}]
    puts $metaFp "node_elev $nL [format %.4f $zz]"
    if {$j < $totalElems} {
        set zz [expr {$zz + [lindex $elemThick $j]}]
    }
}
puts $metaFp "#"
puts $metaFp "# Element -> layer mapping:"
for {set j 0} {$j < $totalElems} {incr j} {
    set eTag [expr {$j + 1}]
    set mTag [lindex $elemMatTag $j]
    set st   [lindex $elemType $j]
    puts $metaFp "elem_info $eTag mat=$mTag type=$st Vs=[lindex $elemVs $j] thick=[lindex $elemThick $j]"
}
close $metaFp

puts "\n============================================================"
puts "DONE — Results in $outputDir"
puts "============================================================"

record
wipe
